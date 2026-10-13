const state = { engineId: null, sensor: "sensor_2", timer: null };

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

function updateEngineOptions(engines, selected) {
  const select = byId("engine-select");
  const existing = Array.from(select.options).map((option) => option.value);
  if (JSON.stringify(existing) !== JSON.stringify(engines)) {
    select.innerHTML = engines.map((engine) => `<option value="${escapeHtml(engine)}">${escapeHtml(engine)}</option>`).join("");
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

function renderEvents(events) {
  byId("event-count").textContent = String(events.length);
  const body = byId("event-body");
  if (!events.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty">No events received</td></tr>';
    return;
  }
  body.innerHTML = events.map((event) => {
    const stateValue = event.state || event.severity || event.action || "--";
    const detail = event.message || event.reason || event.source || "--";
    return `<tr><td>${escapeHtml(formatTime(event.timestamp))}</td><td>${escapeHtml(event.kind)}</td><td>${escapeHtml(stateValue)}</td><td>${escapeHtml(detail)}</td></tr>`;
  }).join("");
}

function render(snapshot) {
  const connected = snapshot.connected;
  byId("connection").className = connected ? "connection online" : "connection";
  byId("connection").lastChild.textContent = connected ? "Connected" : "Offline";
  updateEngineOptions(snapshot.engines, snapshot.engine_id);
  state.engineId = snapshot.engine_id;

  const telemetry = snapshot.telemetry || [];
  const predictions = snapshot.predictions || [];
  const latestTelemetry = telemetry[telemetry.length - 1];
  const latestPrediction = predictions[predictions.length - 1];
  const status = snapshot.status;
  updateSensorOptions(telemetry);

  byId("cycle-value").textContent = latestTelemetry?.cycle ?? "--";
  byId("engine-value").textContent = state.engineId || "Waiting for telemetry";
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
  renderEvents(snapshot.events || []);
}

async function refresh() {
  const query = state.engineId ? `?engine_id=${encodeURIComponent(state.engineId)}` : "";
  try {
    const response = await fetch(`/api/snapshot${query}`, { cache: "no-store" });
    render(await response.json());
  } catch (error) {
    byId("connection").className = "connection";
    byId("connection").lastChild.textContent = "Offline";
  }
}

async function sendCommand(action) {
  if (!state.engineId) return;
  const buttons = [byId("schedule-button"), byId("reset-button")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ engine_id: state.engineId, action })
    });
    const payload = await response.json();
    byId("command-result").textContent = response.ok ? `Command accepted at ${formatTime(payload.timestamp)}` : payload.detail;
  } catch (error) {
    byId("command-result").textContent = "Command could not be delivered";
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

byId("engine-select").addEventListener("change", (event) => { state.engineId = event.target.value; refresh(); });
byId("sensor-select").addEventListener("change", (event) => { state.sensor = event.target.value; refresh(); });
byId("schedule-button").addEventListener("click", () => sendCommand("schedule_maintenance"));
byId("reset-button").addEventListener("click", () => sendCommand("reset_maintenance"));
window.addEventListener("resize", () => refresh());
refresh();
state.timer = window.setInterval(refresh, 700);
