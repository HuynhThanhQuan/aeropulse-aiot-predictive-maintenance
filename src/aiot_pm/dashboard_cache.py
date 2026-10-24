"""Lưu lịch sử dashboard vào SQLite để còn dữ liệu khi MQTT tắt."""

from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .config import project_path

LOGGER = logging.getLogger(__name__)
EVENT_KINDS = ("alert", "command", "status")


def resolve_cache_path() -> Path:
    # Đường dẫn có thể đổi bằng biến môi trường, mặc định nằm trong data/ (không commit).
    return project_path(os.getenv("DASHBOARD_CACHE_PATH", "data/dashboard/cache.sqlite"))


class DashboardCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._queue: queue.Queue[tuple[str, str, int | None, str, str]] = queue.Queue()
        self._pending: list[tuple[str, str, int | None, str, str]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._flush_requested = threading.Event()
        self._flushed = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_flush = time.monotonic()
        self._last_prune = time.monotonic()

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="dashboard-cache", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.flush()
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._drain()

    def enqueue(
        self, engine_id: str, kind: str, cycle: int | None, timestamp: str, payload: dict[str, Any]
    ) -> None:
        # put_nowait để callback MQTT không phải chờ ghi đĩa.
        self._queue.put_nowait((engine_id, kind, cycle, timestamp, json.dumps(payload)))

    def flush(self) -> None:
        # Luồng nền là nơi ghi. Hàm này chỉ đánh thức luồng và chờ hàng đợi trống.
        if self._thread is None or not self._thread.is_alive():
            self._drain()
            return
        self._flushed.clear()
        self._flush_requested.set()
        if not self._flushed.wait(timeout=2):
            self._drain()

    def load(self) -> dict[str, Any]:
        with self._connect() as connection:
            self._ensure_schema(connection)
            telemetry = self._rows_by_engine(connection, "telemetry", 180)
            predictions = self._rows_by_engine(connection, "prediction", 180)
            events = self._events_by_engine(connection, 40)
            status = self._latest_status(connection)
            latest = connection.execute("SELECT ts FROM records ORDER BY id DESC LIMIT 1").fetchone()
            latest_by_engine = dict(connection.execute(
                "SELECT engine_id, ts FROM records WHERE id IN "
                "(SELECT MAX(id) FROM records GROUP BY engine_id)"
            ).fetchall())
        return {
            "telemetry": telemetry,
            "predictions": predictions,
            "events": events,
            "status": status,
            "latest_ts": latest[0] if latest else None,
            "latest_by_engine": latest_by_engine,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                item = None
            now = time.monotonic()
            with self._lock:
                if item is not None:
                    self._pending.append(item)
                force = self._flush_requested.is_set()
                due = bool(self._pending) and (
                    len(self._pending) >= 50 or now - self._last_flush >= 1 or force
                )
                if due:
                    self._write_pending()
                if force and self._queue.empty():
                    self._flush_requested.clear()
                    self._flushed.set()
                if now - self._last_prune >= 60:
                    self._prune()
                    self._last_prune = now

    def _drain(self) -> None:
        drained: list[tuple[str, str, int | None, str, str]] = []
        while True:
            try:
                drained.append(self._queue.get_nowait())
            except queue.Empty:
                break
        with self._lock:
            self._pending.extend(drained)
            self._write_pending()

    def _write_pending(self) -> None:
        if not self._pending:
            return
        batch = list(self._pending)
        self._pending.clear()
        try:
            with self._connect() as connection:
                self._ensure_schema(connection)
                connection.executemany(
                    """
                    INSERT INTO records (engine_id, kind, cycle, ts, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    batch,
                )
            self._last_flush = time.monotonic()
        except sqlite3.Error:
            LOGGER.exception("Could not write dashboard cache")

    def _prune(self) -> None:
        # Giữ 2000 dòng mới nhất cho mỗi cặp động cơ và loại tin.
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    DELETE FROM records
                    WHERE id IN (
                        SELECT id FROM (
                            SELECT id, ROW_NUMBER() OVER (
                                PARTITION BY engine_id, kind ORDER BY id DESC
                            ) AS rn
                            FROM records
                        )
                        WHERE rn > 2000
                    )
                    """
                )
        except sqlite3.Error:
            LOGGER.exception("Could not prune dashboard cache")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY,
                engine_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                cycle INTEGER,
                ts TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_records_engine_kind_id
            ON records (engine_id, kind, id)
            """
        )

    @staticmethod
    def _rows_by_engine(
        connection: sqlite3.Connection, kind: str, limit: int
    ) -> dict[str, list[dict[str, Any]]]:
        rows = connection.execute(
            """
            SELECT engine_id, payload_json FROM (
                SELECT engine_id, payload_json, id,
                       ROW_NUMBER() OVER (PARTITION BY engine_id ORDER BY id DESC) AS rn
                FROM records
                WHERE kind = ?
            )
            WHERE rn <= ?
            ORDER BY id ASC
            """,
            (kind, limit),
        ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for engine_id, payload_json in rows:
            grouped.setdefault(engine_id, []).append(json.loads(payload_json))
        return grouped

    @staticmethod
    def _events_by_engine(
        connection: sqlite3.Connection, limit: int
    ) -> dict[str, list[dict[str, Any]]]:
        placeholders = ",".join("?" for _ in EVENT_KINDS)
        rows = connection.execute(
            f"""
            SELECT engine_id, kind, payload_json FROM (
                SELECT engine_id, kind, payload_json, id,
                       ROW_NUMBER() OVER (PARTITION BY engine_id ORDER BY id DESC) AS rn
                FROM records
                WHERE kind IN ({placeholders})
            )
            WHERE rn <= ?
            ORDER BY id ASC
            """,
            (*EVENT_KINDS, limit),
        ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for engine_id, kind, payload_json in rows:
            payload = json.loads(payload_json)
            grouped.setdefault(engine_id, []).append({"kind": kind, **payload})
        return grouped

    @staticmethod
    def _latest_status(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT engine_id, payload_json FROM (
                SELECT engine_id, payload_json,
                       ROW_NUMBER() OVER (PARTITION BY engine_id ORDER BY id DESC) AS rn
                FROM records
                WHERE kind = 'status'
            )
            WHERE rn = 1
            """
        ).fetchall()
        return {engine_id: json.loads(payload_json) for engine_id, payload_json in rows}
