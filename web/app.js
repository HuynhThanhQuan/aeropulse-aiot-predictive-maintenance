const state = {
  engineId: null,
  sensor: "sensor_2",
  timer: null,
  delay: 700,
  mode: "offline",
  serverReachable: true,
  requestSerial: 0,
  commandNotes: {}
};

const byId = (id) => document.getElementById(id);
const formatTime = (value) => value ? new Date(value).toLocaleTimeString() : "--";
const formatPercent = (value) => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "--";
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[character]));

function drawLineChart(canvas, points, options = {}) {
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  const width = rect.width;
  const height = rect.height;
  const padding = { top: 14, right: 14, bottom: 28, left: 46 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const values = points.map((point) => point.value).filter(Number.isFinite);
  const minimum = options.min ?? (values.length ? Math.min(...values) : 0);
  const maximum = options.max ?? (values.length ? Math.max(...values) : 1);
  const range = Math.max(maximum - minimum, 0.0001);

  context.clearRect(0, 0, width, height);
  context.font = "11px system-ui";
  context.fillStyle = "#718080";
  context.strokeStyle = "#e2e7e7";
  context.lineWidth = 1;
  for (let index = 0; index <= 4; index += 1) {
    const y = padding.top + (plotHeight * index) / 4;
    context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
    const label = maximum - (range * index) / 4;
    context.fillText(options.percent ? `${Math.round(label * 100)}%` : label.toFixed(1), 4, y + 4);
  }
  if (points.length < 2) {
    context.fillStyle = "#718080";
    context.textAlign = "center";
    context.fillText("Waiting for data", padding.left + plotWidth / 2, padding.top + plotHeight / 2);
    context.textAlign = "start";
    return;
  }
  context.beginPath();
  context.strokeStyle = options.color || "#0f766e";
  context.lineWidth = 2.25;
  points.forEach((point, index) => {
    const x = padding.left + (plotWidth * index) / Math.max(points.length - 1, 1);
    const y = padding.top + plotHeight * (1 - (point.value - minimum) / range);
    if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
  });
  context.stroke();
  context.fillStyle = "#718080";
  context.fillText(`cycle ${points[0].cycle}`, padding.left, height - 7);
  context.textAlign = "right";
  context.fillText(`cycle ${points[points.length - 1].cycle}`, width - padding.right, height - 7);
  context.textAlign = "start";
}

function updateEngineOptions(engines, selected, splits) {
  const select = byId("engine-select");
  const existing = Array.from(select.options).map((option) => option.value);
  if (JSON.stringify(existing) !== JSON.stringify(engines) || select.dataset.splits !== JSON.stringify(splits)) {
    select.dataset.splits = JSON.stringify(splits);
    select.innerHTML = engines.map((engine) => `<option value="${escapeHtml(engine)}">${escapeHtml(engine)} · ${escapeHtml(splits[engine] || "Unknown")}</option>`).join("");
  }
  if (selected) select.value = selected;
}

function updateSensorOptions(telemetry) {
  if (!telemetry.length) return;
  const sensors = Object.keys(telemetry[telemetry.length - 1].sensors || {});
  const select = byId("sensor-select");
  if (!sensors.includes(state.sensor)) state.sensor = sensors[0];
  if (select.options.length !== sensors.length) {
    select.innerHTML = sensors.map((sensor) => `<option value="${sensor}">${sensor.replace("_", " ")}</option>`).join("");
  }
  select.value = state.sensor;
}

function describeEvent(event) {
  const kind = event.kind;
  const reason = String(event.reason || "");
  let type = "Sự kiện";
  let tone = "other";
  let content = event.message || reason || event.action || event.state || kind || "Sự kiện khác";
  if (kind === "alert") {
    type = "Cảnh báo";
    tone = "alert";
    content = event.severity === "high"
      ? "AI phát hiện rủi ro vượt ngưỡng cảnh báo"
      : (event.message || "Cảnh báo từ edge");
  } else if (kind === "command" && event.action === "schedule_maintenance") {
    type = "Lệnh";
    tone = "command";
    content = "Đã gửi yêu cầu lên lịch bảo trì";
  } else if (kind === "command" && event.action === "reset_maintenance") {
    type = "Lệnh";
    tone = "command";
    content = "Đã gửi yêu cầu xóa cờ bảo trì";
  } else if (kind === "command") {
    type = "Lệnh";
    tone = "command";
  } else if (kind === "status" && event.state === "maintenance_scheduled") {
    type = "Phản hồi";
    tone = "status";
    content = "Thiết bị xác nhận đã lên lịch bảo trì";
  } else if (kind === "status" && event.state === "running") {
    type = "Phản hồi";
    tone = "status";
    content = reason.toLowerCase().includes("maintenance flag reset")
      ? "Thiết bị xác nhận đã xóa cờ bảo trì"
      : "Thiết bị đang chạy";
  } else if (kind === "status" && event.state === "restarting_replay") {
    type = "Trạng thái";
    tone = "status";
    content = "Đã hết dữ liệu, chuẩn bị phát lại từ đầu";
  } else if (kind === "status" && event.state === "online") {
    type = "Trạng thái";
    tone = "status";
    content = "Simulator đã kết nối";
  } else if (kind === "status" && event.state === "offline") {
    type = "Trạng thái";
    tone = "status";
    content = "Simulator thông báo ngừng hoạt động";
  } else if (kind === "status") {
    type = "Trạng thái";
    tone = "status";
  }
  let source = "—";
  if (event.source === "automatic_closed_loop_controller") source = "Tự động";
  else if (event.source === "operator_dashboard") source = "Thủ công";
  return { type, tone, content, source };
}

function renderEvents(events, engineId) {
  byId("event-count").textContent = String(events.length);
  const body = byId("event-body");
  if (!events.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty">No events received</td></tr>';
    return;
  }
  body.innerHTML = events.map((event) => {
    const described = describeEvent(event);
    return `<tr class="event-${described.tone}"><td>${escapeHtml(formatTime(event.timestamp))}</td><td>${escapeHtml(engineId || event.engine_id || "--")}</td><td>${escapeHtml(described.type)}</td><td>${escapeHtml(described.content)}</td><td>${escapeHtml(described.source)}</td></tr>`;
  }).join("");
}

function showWaiting(engineId) {
  byId("cycle-value").textContent = "--";
  byId("engine-value").textContent = engineId || "Waiting for telemetry";
  byId("risk-value").textContent = "--";
  byId("probability-value").textContent = "No prediction yet";
  byId("latency-value").textContent = "--";
  byId("maintenance-value").textContent = "--";
  byId("state-value").textContent = "Awaiting device state";
  byId("controller-state").textContent = "Monitoring";
  const badge = byId("risk-badge");
  badge.className = "badge badge-neutral";
  badge.textContent = "NO DATA";
  drawLineChart(byId("risk-chart"), [], { min: 0, max: 1, percent: true });
  drawLineChart(byId("sensor-chart"), [], { color: "#276a9b" });
  renderEvents([], engineId);
  byId("command-result").textContent = state.commandNotes[engineId] || "";
}

function renderConnection(snapshot) {
  const mode = snapshot.mode || "offline";
  state.mode = mode;
  const connection = byId("connection");
  connection.className = `connection mode-${mode}`;
  const label = byId("connection-label");
  const detail = byId("connection-detail");
  if (mode === "live") {
    label.textContent = "LIVE";
    detail.textContent = "";
  } else if (mode === "stale") {
    const seconds = Math.max(0, Math.floor(snapshot.seconds_since_last_message || 0));
    label.textContent = "STALE";
    detail.textContent = `no new data for ${seconds} s`;
  } else {
    label.textContent = "OFFLINE";
    const updated = formatTime(snapshot.last_message_at);
    detail.textContent = updated === "--"
      ? "pipeline stopped, showing last saved data"
      : `pipeline stopped, showing last saved data, last update ${updated}`;
  }
  const offline = mode === "offline" || !state.serverReachable;
  byId("schedule-button").disabled = offline;
  byId("reset-button").disabled = offline;
}

function render(snapshot) {
  state.serverReachable = true;
  byId("server-error").hidden = true;
  renderConnection(snapshot);
  const selected = state.engineId && (snapshot.engines || []).includes(state.engineId)
    ? state.engineId
    : snapshot.engine_id;
  updateEngineOptions(snapshot.engines, selected, snapshot.engine_splits || {});
  state.engineId = selected;

  const telemetry = snapshot.telemetry || [];
  const predictions = snapshot.predictions || [];
  const latestTelemetry = telemetry[telemetry.length - 1];
  const latestPrediction = predictions[predictions.length - 1];
  const status = snapshot.status;
  updateSensorOptions(telemetry);

  byId("cycle-value").textContent = latestTelemetry?.cycle ?? "--";
  byId("engine-value").textContent = state.engineId
    ? `${state.engineId} · ${snapshot.dataset_split || "Unknown"}`
    : "Waiting for telemetry";
  byId("risk-value").textContent = latestPrediction ? latestPrediction.risk_level.toUpperCase() : "--";
  byId("probability-value").textContent = latestPrediction ? `${formatPercent(latestPrediction.failure_probability)} probability` : "No prediction yet";
  byId("latency-value").textContent = latestPrediction ? `${latestPrediction.latency_ms.toFixed(2)} ms` : "--";
  byId("maintenance-value").textContent = status ? (status.maintenance_scheduled ? "SCHEDULED" : "CLEAR") : "--";
  byId("state-value").textContent = status?.state?.replaceAll("_", " ") || "Awaiting device state";
  byId("controller-state").textContent = status?.maintenance_scheduled ? "Action acknowledged" : "Monitoring";

  const risk = latestPrediction?.risk_level || "neutral";
  const badge = byId("risk-badge");
  badge.className = `badge badge-${risk}`;
  badge.textContent = risk === "neutral" ? "NO DATA" : risk.toUpperCase();

  drawLineChart(byId("risk-chart"), predictions.map((prediction) => ({ cycle: prediction.cycle, value: prediction.failure_probability })), { min: 0, max: 1, percent: true, color: risk === "high" ? "#b42318" : "#0f766e" });
  const sensorPoints = telemetry.map((row) => ({ cycle: row.cycle, value: Number(row.sensors?.[state.sensor]) })).filter((point) => Number.isFinite(point.value));
  drawLineChart(byId("sensor-chart"), sensorPoints, { color: "#276a9b" });
  renderEvents(snapshot.events || [], state.engineId);
  byId("command-result").textContent = state.commandNotes[state.engineId] || "";
}

function scheduleRefresh() {
  window.clearTimeout(state.timer);
  state.timer = window.setTimeout(refresh, state.delay);
}

async function refresh() {
  const requestedId = state.engineId;
  const serial = ++state.requestSerial;
  const query = requestedId ? `?engine_id=${encodeURIComponent(requestedId)}` : "";
  try {
    const response = await fetch(`/api/snapshot${query}`, { cache: "no-store" });
    if (!response.ok) throw new Error("snapshot failed");
    const snapshot = await response.json();
    if (serial !== state.requestSerial) return;
    if (requestedId && snapshot.engine_id !== requestedId) return;
    state.delay = 700;
    render(snapshot);
  } catch (error) {
    // Giữ biểu đồ cũ. Chỉ báo không tới được server và thử lại, lâu nhất 10 giây.
    state.serverReachable = false;
    state.delay = Math.min(state.delay * 2, 10000);
    byId("server-error").hidden = false;
    byId("schedule-button").disabled = true;
    byId("reset-button").disabled = true;
  } finally {
    scheduleRefresh();
  }
}

async function sendCommand(action) {
  const engineId = state.engineId;
  if (!engineId) return;
  const buttons = [byId("schedule-button"), byId("reset-button")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ engine_id: engineId, action })
    });
    const payload = await response.json();
    const note = response.ok ? `Đã gửi yêu cầu lúc ${formatTime(payload.timestamp)}` : payload.detail;
    state.commandNotes[engineId] = note;
    if (state.engineId === engineId) byId("command-result").textContent = note;
  } catch (error) {
    state.commandNotes[engineId] = "Command could not be delivered";
    if (state.engineId === engineId) byId("command-result").textContent = state.commandNotes[engineId];
  } finally {
    const offline = state.mode === "offline" || !state.serverReachable;
    buttons.forEach((button) => { button.disabled = offline; });
  }
}

byId("engine-select").addEventListener("change", (event) => {
  state.engineId = event.target.value;
  showWaiting(state.engineId);
  refresh();
});
byId("sensor-select").addEventListener("change", (event) => { state.sensor = event.target.value; refresh(); });
byId("schedule-button").addEventListener("click", () => sendCommand("schedule_maintenance"));
byId("reset-button").addEventListener("click", () => sendCommand("reset_maintenance"));
const requestedEngine = new URLSearchParams(window.location.search).get("engine");
if (requestedEngine) state.engineId = requestedEngine;
window.addEventListener("resize", () => refresh());
refresh();
