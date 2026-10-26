const $ = (id) => document.getElementById(id);

const state = {
  config: null,
  status: null,
  devices: [],
  samples: [],
  imageBase64: null,
  previewUrl: null,
  previewKey: "",
  previewTimer: null,
  previewRequest: 0,
  busy: false,
  footerSuppressed: false,
};

function setMessage(text, kind = "") {
  const box = $("messageBox");
  box.textContent = text || "";
  box.className = "message" + (kind ? ` ${kind}` : "");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function readError(response) {
  try {
    const data = await response.json();
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg || d).join(", ");
    return JSON.stringify(data);
  } catch {
    return `${response.status} ${response.statusText}`;
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

function payload(extra = {}) {
  return {
    message: $("message").value,
    title: $("title").value.trim() || null,
    style: $("style").value,
    footer: state.footerSuppressed ? "" : $("footer").value.trim() || null,
    qr: $("qr").value.trim() || null,
    image_base64: state.imageBase64,
    invert_image: false,
    density: Number($("density").value),
    feed_dots: Number($("feed").value),
    label_mode: $("labelMode").checked,
    source: "web",
    ...extra,
  };
}

function hasContent() {
  return Boolean(
    $("message").value.trim() ||
      $("title").value.trim() ||
      $("qr").value.trim() ||
      state.imageBase64
  );
}

function setImageBase64(value) {
  state.imageBase64 = value;
  $("clearImage").hidden = !value;
}

function clearPreview() {
  state.previewRequest += 1;
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = null;
  state.previewKey = "";
  $("preview").hidden = true;
  $("preview").removeAttribute("src");
  $("previewEmpty").hidden = false;
  $("downloadLink").hidden = true;
  $("previewMeta").textContent = "waiting";
  $("previewSize").textContent = "";
}

async function refreshPreview(force = false) {
  if (!hasContent()) {
    clearPreview();
    return;
  }
  const body = payload();
  const key = JSON.stringify(body);
  if (!force && key === state.previewKey) return;
  const requestId = ++state.previewRequest;

  try {
    const response = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: key,
    });
    if (!response.ok) throw new Error(await readError(response));
    const blob = await response.blob();
    if (requestId !== state.previewRequest) return;
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = URL.createObjectURL(blob);
    state.previewKey = key;
    const width = response.headers.get("X-Print-Width") || "?";
    const height = response.headers.get("X-Print-Height") || "?";
    $("preview").src = state.previewUrl;
    $("preview").hidden = false;
    $("previewEmpty").hidden = true;
    $("previewMeta").textContent = `${width} × ${height} px`;
    $("previewSize").textContent = `${width} × ${height} px · 1-bit`;
    const link = $("downloadLink");
    link.href = state.previewUrl;
    link.download = `thermal-${Date.now()}.png`;
    link.hidden = false;
  } catch (error) {
    if (requestId !== state.previewRequest) return;
    setMessage(`Preview error: ${error.message}`, "err");
  }
}

function schedulePreview() {
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(() => refreshPreview(), 260);
}

function setBusy(busy) {
  state.busy = busy;
  for (const id of ["printBtn", "dryRunBtn", "previewBtn"]) {
    $(id).disabled = busy;
  }
}

async function printNow(dryRun = false) {
  if (state.busy) return;
  if (!hasContent()) {
    setMessage("Write something first.", "err");
    return;
  }
  setBusy(true);
  setMessage(dryRun ? "Saving preview…" : "Sending to printer…");
  try {
    const result = await api("/api/print", {
      method: "POST",
      body: JSON.stringify(payload({ dry_run: dryRun, copies: 1 })),
    });
    if (dryRun) {
      setMessage("Preview saved to history. Nothing was sent to the printer.", "ok");
    } else {
      const elapsed = result?.job?.duration_ms ?? 0;
      const transport = result?.job?.transport ?? "printer";
      setMessage(`Printed on ${transport} in ${elapsed} ms.`, "ok");
    }
    await Promise.all([loadHistory(), refreshStatus()]);
    await refreshPreview(true);
  } catch (error) {
    setMessage(`${dryRun ? "Save" : "Print"} failed: ${error.message}`, "err");
  } finally {
    setBusy(false);
  }
}

function statusClass(status) {
  if (!status?.connected) return "offline";
  if (status.mock) return "mock";
  if (status.status?.paper_empty || status.status?.cover_open || status.status?.overheating) return "error";
  return "online";
}

function statusLabel(status) {
  if (!status?.connected) return "offline";
  if (status.mock) return "mock printer";
  if (status.status?.paper_empty) return "paper empty";
  if (status.status?.cover_open) return "cover open";
  if (status.status?.overheating) return "overheating";
  return "online";
}

function renderStatus() {
  const status = state.status || {};
  const pill = $("statusPill");
  pill.className = `pill ${statusClass(status)}`;
  pill.textContent = statusLabel(status);
  $("battery").textContent =
    status.battery !== undefined && status.battery !== null ? `${status.battery}%` : "";
  $("connectBtn").textContent = status.connected ? "Disconnect" : "Connect";
  $("transportLabel").textContent = status.transport || state.config?.config?.transport || "local bridge";

  const info = status.info || {};
  const lines = [];
  lines.push(`<b>${escapeHtml(status.label || "Not connected")}</b>`);
  if (status.transport) lines.push(`transport: ${escapeHtml(status.transport)}`);
  if (status.status_text) lines.push(`status: ${escapeHtml(status.status_text)}`);
  if (status.battery !== undefined && status.battery !== null) lines.push(`battery: ${status.battery}%`);
  if (info.model) lines.push(`model: ${escapeHtml(info.model)}`);
  if (info.firmware) lines.push(`firmware: ${escapeHtml(info.firmware)}`);
  if (status.last_error) lines.push(`<span style="color:var(--red)">${escapeHtml(status.last_error)}</span>`);
  $("printerInfo").innerHTML = lines.join("<br />");
}

async function refreshStatus() {
  try {
    state.status = await api("/api/status");
    renderStatus();
  } catch (error) {
    state.status = { connected: false, last_error: error.message };
    renderStatus();
  }
}

function renderDevices() {
  const list = $("deviceList");
  list.replaceChildren();
  if (!state.devices.length) return;
  for (const device of state.devices) {
    const row = document.createElement("div");
    row.className = "device";
    const meta = document.createElement("div");
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = device.name || "(unnamed)";
    if (device.likely_printer) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "likely";
      name.appendChild(badge);
    }
    const addr = document.createElement("div");
    addr.className = "addr";
    addr.textContent = `${device.address}${device.rssi != null ? ` · ${device.rssi} dBm` : ""}`;
    meta.append(name, addr);
    const button = document.createElement("button");
    button.textContent = "Connect";
    button.addEventListener("click", () => connectDevice(device));
    row.append(meta, button);
    list.appendChild(row);
  }
}

async function scan() {
  setMessage("Scanning for Bluetooth printers…");
  $("deviceList").replaceChildren();
  try {
    const result = await api("/api/devices?timeout=8");
    state.devices = result.devices || [];
    renderDevices();
    if (!state.devices.length) {
      setMessage("No BLE devices found. Wake the printer, turn off the phone app, and try again.", "err");
    } else if (!state.devices.some((d) => d.likely_printer)) {
      setMessage(
        `Found ${state.devices.length} device(s), but no obvious printer. Look for "MXW01" or "C&Co 3128_BLE", or try waking the printer and scanning again.`,
        "err"
      );
    } else {
      const likely = state.devices.filter((d) => d.likely_printer).length;
      setMessage(`Found ${state.devices.length} device(s), ${likely} likely printer(s). Pick one below.`, "ok");
    }
  } catch (error) {
    setMessage(`Scan failed: ${error.message}`, "err");
  }
}

async function connectDevice(device) {
  setMessage(`Connecting to ${device.name || device.address}…`);
  try {
    await api("/api/connect", {
      method: "POST",
      body: JSON.stringify({
        transport: "ble",
        address: device.address,
        name: device.name && device.name !== "(unnamed)" ? device.name : null,
        persist: true,
      }),
    });
    $("deviceList").replaceChildren();
    await refreshStatus();
    setMessage("Connected.", "ok");
  } catch (error) {
    setMessage(`Connection failed: ${error.message}`, "err");
  }
}

async function connectMock() {
  setMessage("Starting mock printer…");
  try {
    await api("/api/connect", {
      method: "POST",
      body: JSON.stringify({ transport: "mock", persist: false }),
    });
    await refreshStatus();
    setMessage("Mock printer connected. Prints are saved to the history as PNGs.", "ok");
  } catch (error) {
    setMessage(`Could not start mock: ${error.message}`, "err");
  }
}

async function disconnect() {
  try {
    await api("/api/disconnect", { method: "POST", body: "{}" });
    await refreshStatus();
    setMessage("Disconnected.", "ok");
  } catch (error) {
    setMessage(`Disconnect failed: ${error.message}`, "err");
  }
}

async function loadHistory() {
  try {
    const result = await api("/api/history?limit=24");
    const jobs = result.jobs || [];
    const list = $("historyList");
    list.replaceChildren();
    $("historyCount").textContent = jobs.length ? `${jobs.length} shown` : "";
    if (!jobs.length) {
      const empty = document.createElement("div");
      empty.className = "history-empty";
      empty.textContent = "Nothing printed yet.";
      list.appendChild(empty);
      return;
    }
    for (const job of jobs) {
      const link = document.createElement("a");
      link.className = "history-item";
      link.href = job.preview_url;
      link.target = "_blank";
      link.rel = "noopener";
      const img = document.createElement("img");
      img.src = job.preview_url;
      img.alt = "";
      img.loading = "lazy";
      const meta = document.createElement("div");
      meta.className = "meta";
      const title = document.createElement("span");
      title.className = "title";
      title.textContent = job.title || `(${job.style})`;
      const sub = document.createElement("span");
      sub.className = "sub";
      const when = new Date(job.created_at);
      const time = Number.isNaN(when.getTime()) ? job.created_at : when.toLocaleString();
      sub.textContent = `${job.style} · ${job.width}×${job.height} · ${job.dry_run ? "preview" : "printed"}${job.source ? ` · ${job.source}` : ""}`;
      const sub2 = document.createElement("span");
      sub2.className = "sub";
      sub2.textContent = time;
      meta.append(title, sub, sub2);
      link.append(img, meta);
      list.appendChild(link);
    }
  } catch (error) {
    $("historyList").textContent = `Could not load history: ${error.message}`;
  }
}

function renderSamples() {
  const wrap = $("inspiration");
  wrap.replaceChildren();
  const label = document.createElement("span");
  label.textContent = "Try:";
  wrap.appendChild(label);
  state.samples.forEach((sample) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = sample.label;
    button.addEventListener("click", () => {
      $("title").value = sample.title || "";
      $("message").value = sample.message || "";
      $("style").value = sample.style || "note";
      $("footer").value = sample.footer ?? "";
      $("qr").value = "";
      state.footerSuppressed = sample.footer === "";
      setImageBase64(null);
      $("image").value = "";
      schedulePreview();
    });
    wrap.appendChild(button);
  });
}

async function loadConfig() {
  try {
    const [result, templates] = await Promise.all([
      api("/api/config"),
      api("/api/templates"),
    ]);
    state.config = result;
    const config = result.config || {};
    if (config.density !== undefined) {
      $("density").value = config.density;
      $("densityVal").textContent = config.density;
    }
    if (config.feed_dots !== undefined) {
      const feed = Math.min(200, config.feed_dots);
      $("feed").value = feed;
      $("feedVal").textContent = feed;
    }
    $("labelMode").checked = Boolean(config.label_mode);
    state.samples = templates.samples || [];
    renderSamples();
    renderStatus();
  } catch (error) {
    setMessage(`Could not load config: ${error.message}`, "err");
  }
}

function wireEvents() {
  for (const id of ["title", "message", "qr"]) {
    $(id).addEventListener("input", schedulePreview);
  }
  $("footer").addEventListener("input", () => {
    // Typing a footer overrides a sample's "hide footer" choice.
    state.footerSuppressed = false;
    schedulePreview();
  });
  $("style").addEventListener("change", schedulePreview);
  $("density").addEventListener("input", () => {
    $("densityVal").textContent = $("density").value;
  });
  $("feed").addEventListener("input", () => {
    $("feedVal").textContent = $("feed").value;
  });
  $("labelMode").addEventListener("change", schedulePreview);
  $("image").addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    if (!file) {
      setImageBase64(null);
      schedulePreview();
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setImageBase64(reader.result);
      schedulePreview();
    };
    reader.readAsDataURL(file);
  });
  $("clearImage").addEventListener("click", () => {
    $("image").value = "";
    setImageBase64(null);
    schedulePreview();
  });
  $("previewBtn").addEventListener("click", () => refreshPreview(true));
  $("printBtn").addEventListener("click", () => printNow(false));
  $("dryRunBtn").addEventListener("click", () => printNow(true));
  $("connectBtn").addEventListener("click", () => {
    if (state.status?.connected) disconnect();
    else scan();
  });
  $("scanBtn").addEventListener("click", scan);
  $("mockBtn").addEventListener("click", connectMock);
  $("disconnectBtn").addEventListener("click", disconnect);

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      printNow(false);
    }
  });
}

async function boot() {
  wireEvents();
  await loadConfig();
  await refreshStatus();
  await loadHistory();
  // Refresh hardware status after the first paint.
  setTimeout(refreshStatus, 500);
  setInterval(refreshStatus, 15000);
}

boot();
