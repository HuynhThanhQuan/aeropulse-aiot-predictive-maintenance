const COLORS = {
  normal: "#3cb371",
  warning: "#f0b429",
  critical: "#e36a4a",
  unknown: "#c5ced0",
};
const SERIES_COLORS = ["#3b82f6", "#22a06b", "#e09a2b", "#8b5cf6", "#0ea5e9"];
const THRESHOLD_COLOR = "#94a3b8";

const state = {
  window: "15m",
  top: 3,
  engines: [],
  trend: null,
  selected: null,
  timer: null,
  refreshSeconds: 10,
  refreshing: false,
};

const byId = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"
}[character]));

function shortId(engineId) {
  const match = /^FD001-(\d+)$/.exec(engineId || "");
  return match ? match[1] : engineId;
}

function healthLabel(health) {
  if (health === "unknown") return "Unknown";
  return health.charAt(0).toUpperCase() + health.slice(1);
}

async function readJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.detail || "Fleet request failed");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function setStatus(mode, label, detail) {
  const node = byId("refresh-state");
  node.className = `connection mode-${mode}`;
  byId("refresh-label").textContent = label;
  byId("refresh-detail").textContent = detail;
}

function renderSummary(summary) {
  state.refreshSeconds = summary.refresh_seconds || 10;
  byId("kpi-reporting").textContent = summary.total_engines
    ? `${summary.reporting_engines} / ${summary.total_engines}`
    : "--";
  byId("kpi-reporting-detail").textContent = summary.total_engines
    ? `${summary.reporting_percent.toFixed(1)}% reporting`
    : "Insufficient data";
  byId("kpi-critical").textContent = String(summary.critical_engines);
  byId("kpi-stale").textContent = String(summary.stale_engines);
  byId("kpi-stale-detail").textContent = `older than ${Math.round(summary.stale_after_seconds)} s`;
  byId("kpi-exposure").textContent = `${summary.risk_exposure_percent.toFixed(1)}%`;
  byId("kpi-exposure").className = summary.risk_exposure_percent > 0 ? "tone-critical" : "";
  byId("insights").innerHTML = (summary.insights || ["Insufficient data"])
    .map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  byId("registry-note").textContent = summary.registry_note || "";
  setStatus("live", "LIVE", `refresh ${state.refreshSeconds}s`);
}

function renderDonut(distribution) {
  const canvas = byId("donut");
  const ratio = window.devicePixelRatio || 1;
  const size = 220;
  canvas.width = size * ratio;
  canvas.height = size * ratio;
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  const total = distribution.total || 0;
  const parts = ["critical", "normal", "unknown", "warning"].map((key) => ({
    key, count: distribution[key] || 0, color: COLORS[key],
  }));
  const center = size / 2;
  context.clearRect(0, 0, size, size);
  if (!total) {
    context.beginPath();
    context.strokeStyle = "#e6ecec";
    context.lineWidth = 28;
    context.arc(center, center, 72, 0, Math.PI * 2);
    context.stroke();
  } else {
    let angle = -Math.PI / 2;
    parts.forEach((part) => {
      if (!part.count) return;
      const sweep = (Math.PI * 2 * part.count) / total;
      context.beginPath();
      context.strokeStyle = part.color;
      context.lineWidth = 32;
      context.arc(center, center, 72, angle, angle + sweep);
      context.stroke();
      angle += sweep;
    });
  }
  byId("donut-legend").innerHTML = ["critical", "normal", "unknown", "warning"].map((key) => {
    const count = distribution[key] || 0;
    const percent = total ? ((100 * count) / total).toFixed(1) : "0.0";
    return `<li><i class="swatch" style="background:${COLORS[key]}"></i>${escapeHtml(healthLabel(key))} · ${count} · ${percent}%</li>`;
  }).join("");
}

function renderRanking(payload) {
  const rows = payload.ranking || [];
  byId("ranking-meta").textContent = payload.contributing_reporting_engines
    ? `${payload.contributing_reporting_engines} reporting engines · top ${payload.ranking_limit}`
    : "No reporting engines";
  if (!rows.length) {
    byId("ranking").innerHTML = '<p class="empty-copy">Insufficient data</p>';
    return;
  }
  byId("ranking").innerHTML = rows.map((engine) => {
    const width = Math.max(0, Math.min(100, engine.failure_percent));
    return `<div class="bar-row" title="${escapeHtml(engine.engine_id)} ${engine.failure_percent}% ${escapeHtml(engine.health)}">
      <span>${escapeHtml(shortId(engine.engine_id))}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${width}%;background:${COLORS[engine.health] || COLORS.unknown}"></div></div>
      <strong>${engine.failure_percent.toFixed(1)}%</strong>
    </div>`;
  }).join("");
}

function visibleEngines() {
  const query = byId("engine-search").value.trim().toLowerCase();
  const status = byId("status-filter").value;
  const sort = byId("engine-sort").value;
  let engines = state.engines.filter((engine) => {
    const matchesQuery = !query || engine.engine_id.toLowerCase().includes(query);
    const matchesStatus = status === "all" || engine.health === status;
    return matchesQuery && matchesStatus;
  });
  const rank = { critical: 0, warning: 1, normal: 2, unknown: 3 };
  engines.sort((left, right) => {
    if (sort === "risk") return right.failure_probability - left.failure_probability;
    if (sort === "status") return rank[left.health] - rank[right.health] || left.engine_id.localeCompare(right.engine_id);
    return left.engine_id.localeCompare(right.engine_id);
  });
  return engines;
}

function renderGrid() {
  const engines = visibleEngines();
  if (!engines.length) {
    byId("engine-grid").innerHTML = '<p class="empty-copy">Insufficient data</p>';
    return;
  }
  byId("engine-grid").innerHTML = engines.map((engine) => {
    const pill = engine.connectivity === "stale" ? "stale" : engine.health;
    const label = engine.connectivity === "stale" ? "Stale" : healthLabel(engine.health);
    const selected = state.selected === engine.engine_id ? " is-selected" : "";
    return `<button class="tile${selected}" type="button" data-engine="${escapeHtml(engine.engine_id)}">
      <strong>${escapeHtml(shortId(engine.engine_id))}</strong>
      <span class="pill pill-${pill}">${escapeHtml(label)}</span>
      <small>${engine.failure_percent.toFixed(1)}%</small>
    </button>`;
  }).join("");
}

function renderDetail(engine) {
  const panel = byId("engine-detail");
  if (!engine) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  const rows = [
    ["Engine", engine.engine_id],
    ["Health", healthLabel(engine.health)],
    ["Connectivity", engine.connectivity],
    ["Failure probability", `${engine.failure_percent.toFixed(1)}%`],
    ["Last known health", healthLabel(engine.last_known_health)],
    ["Cycle", engine.cycle ?? "--"],
    ["Risk tag", engine.risk_level || "--"],
    ["Latency", engine.latency_ms == null ? "--" : `${engine.latency_ms.toFixed(2)} ms`],
    ["Predicted class", engine.predicted_class ?? "--"],
    ["Age", `${engine.age_seconds} s`],
  ];
  panel.hidden = false;
  panel.innerHTML = `<strong>${escapeHtml(engine.engine_id)}</strong>
    <div class="detail-grid">${rows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join("")}</div>
    <p class="note">Latest stored prediction. A stale engine stays Unknown until new data arrives.</p>
    <div class="detail-actions">
      <a href="/?engine=${encodeURIComponent(engine.engine_id)}">Open engine console</a>
      <button id="close-detail" type="button">Close</button>
    </div>`;
  byId("close-detail").addEventListener("click", () => {
    state.selected = null;
    renderGrid();
    renderDetail(null);
  });
}

function drawTrend(trend) {
  const canvas = byId("trend");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  const width = rect.width;
  const height = rect.height;
  const padding = { top: 16, right: 16, bottom: 28, left: 42 };
  context.clearRect(0, 0, width, height);
  context.font = "11px system-ui";
  context.strokeStyle = "#e6ecec";
  context.fillStyle = "#718080";
  for (let index = 0; index <= 4; index += 1) {
    const y = padding.top + ((height - padding.top - padding.bottom) * index) / 4;
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
    context.fillText(`${100 - index * 25}%`, 4, y + 4);
  }
  const series = trend?.series || [];
  const stamps = series.flatMap((item) => item.points.map((point) => Date.parse(point.timestamp))).filter(Number.isFinite);
  if (stamps.length < 2) {
    context.fillText("Insufficient data", padding.left + 8, height / 2);
    byId("trend-legend").innerHTML = "";
    return;
  }
  const minTime = Math.min(...stamps);
  const maxTime = Math.max(...stamps);
  const span = Math.max(maxTime - minTime, 1);
  const xFor = (time) => padding.left + ((width - padding.left - padding.right) * (time - minTime)) / span;
  const yFor = (percent) => padding.top + ((height - padding.top - padding.bottom) * (1 - percent / 100));
  const threshold = (trend.alert_threshold || 0) * 100;
  context.save();
  context.setLineDash([5, 4]);
  context.strokeStyle = THRESHOLD_COLOR;
  context.beginPath();
  context.moveTo(padding.left, yFor(threshold));
  context.lineTo(width - padding.right, yFor(threshold));
  context.stroke();
  context.restore();
  series.forEach((item, seriesIndex) => {
    const color = SERIES_COLORS[seriesIndex % SERIES_COLORS.length];
    const bySegment = new Map();
    item.points.forEach((point) => {
      const bucket = bySegment.get(point.segment) || [];
      bucket.push(point);
      bySegment.set(point.segment, bucket);
    });
    context.strokeStyle = color;
    context.lineWidth = 2;
    bySegment.forEach((points) => {
      context.beginPath();
      points.forEach((point, index) => {
        const x = xFor(Date.parse(point.timestamp));
        const y = yFor(point.failure_percent);
        if (index === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.stroke();
    });
  });
  context.fillStyle = "#718080";
  context.fillText(new Date(minTime).toLocaleTimeString(), padding.left, height - 8);
  context.textAlign = "right";
  context.fillText(new Date(maxTime).toLocaleTimeString(), width - padding.right, height - 8);
  context.textAlign = "start";
  const legend = series.map((item, index) =>
    `<li><i class="swatch" style="background:${SERIES_COLORS[index % SERIES_COLORS.length]}"></i>${escapeHtml(shortId(item.engine_id))}</li>`
  );
  legend.push(`<li><i class="swatch" style="background:${THRESHOLD_COLOR}"></i>threshold ${(threshold).toFixed(0)}%</li>`);
  byId("trend-legend").innerHTML = legend.join("");
  byId("replay-note").textContent = trend.replay_note || "";
  canvas._trend = { series, minTime, span, padding, width, height };
}

function renderHistogram(distribution) {
  const bins = distribution.bins || [];
  const peak = Math.max(1, ...bins.map((bin) => bin.count));
  byId("distribution-meta").textContent = `${distribution.reporting_engines || 0} reporting engines`;
  byId("sample-note").textContent = distribution.note || "";
  byId("histogram").innerHTML = bins.map((bin) => {
    const height = Math.round((bin.count / peak) * 100);
    return `<div class="hist-col" title="${escapeHtml(bin.label)}: ${bin.count}">
      <b>${bin.count}</b>
      <div class="hist-bar" style="height:${height}%"></div>
      <span>${escapeHtml(bin.label)}</span>
    </div>`;
  }).join("");
}

async function refreshTrend() {
  state.trend = await readJson(`/api/fleet/risk-trend?window=${state.window}&top=${state.top}`);
  drawTrend(state.trend);
}

async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const [summary, engines, distribution] = await Promise.all([
      readJson("/api/fleet/summary"),
      readJson("/api/fleet/engines"),
      readJson("/api/fleet/risk-distribution"),
    ]);
    byId("server-error").hidden = true;
    renderSummary(summary);
    renderDonut(engines.health_distribution);
    renderRanking(engines);
    state.engines = engines.engines || [];
    renderGrid();
    renderHistogram(distribution);
    if (state.selected) {
      const current = state.engines.find((engine) => engine.engine_id === state.selected);
      renderDetail(current || null);
    }
    await refreshTrend();
  } catch (error) {
    setStatus("offline", "OFFLINE", error.message || "Cannot reach fleet data");
    byId("server-error").hidden = false;
    byId("server-error").textContent = error.message || "Cannot reach fleet data";
    if (!state.engines.length) {
      byId("insights").innerHTML = "<li>Insufficient data</li>";
    }
  } finally {
    state.refreshing = false;
    window.clearTimeout(state.timer);
    state.timer = window.setTimeout(refresh, state.refreshSeconds * 1000);
  }
}

byId("engine-grid").addEventListener("click", async (event) => {
  const tile = event.target.closest("[data-engine]");
  if (!tile) return;
  state.selected = tile.dataset.engine;
  renderGrid();
  try {
    renderDetail(await readJson(`/api/fleet/engines/${encodeURIComponent(state.selected)}`));
  } catch (error) {
    renderDetail(state.engines.find((engine) => engine.engine_id === state.selected) || null);
  }
});
byId("engine-search").addEventListener("input", renderGrid);
byId("status-filter").addEventListener("change", renderGrid);
byId("engine-sort").addEventListener("change", renderGrid);
document.querySelectorAll("[data-window]").forEach((button) => {
  button.addEventListener("click", async () => {
    state.window = button.dataset.window;
    document.querySelectorAll("[data-window]").forEach((item) => item.classList.toggle("is-active", item === button));
    try { await refreshTrend(); } catch (error) { byId("replay-note").textContent = error.message; }
  });
});
document.querySelectorAll("[data-top]").forEach((button) => {
  button.addEventListener("click", async () => {
    state.top = Number(button.dataset.top);
    document.querySelectorAll("[data-top]").forEach((item) => item.classList.toggle("is-active", item === button));
    try { await refreshTrend(); } catch (error) { byId("replay-note").textContent = error.message; }
  });
});
window.addEventListener("resize", () => { if (state.trend) drawTrend(state.trend); });
byId("trend").addEventListener("mousemove", (event) => {
  const stored = byId("trend")._trend;
  if (!stored) return;
  const bounds = byId("trend").getBoundingClientRect();
  const x = event.clientX - bounds.left;
  const plot = stored.width - stored.padding.left - stored.padding.right;
  const ratio = Math.min(1, Math.max(0, (x - stored.padding.left) / plot));
  const time = stored.minTime + ratio * stored.span;
  let nearest = null;
  stored.series.forEach((item) => {
    item.points.forEach((point) => {
      const distance = Math.abs(Date.parse(point.timestamp) - time);
      if (!nearest || distance < nearest.distance) nearest = { distance, point, engine: item.engine_id };
    });
  });
  byId("trend").title = nearest
    ? `${shortId(nearest.engine)} ${nearest.point.failure_percent}% at ${new Date(nearest.point.timestamp).toLocaleTimeString()}`
    : "";
});
refresh();
