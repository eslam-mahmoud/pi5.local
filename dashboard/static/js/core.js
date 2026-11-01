// Shared helpers: DOM building, API calls, formatting, sheets and polling.

export const $ = (selector) => document.querySelector(selector);

// Builds DOM nodes. Text always goes through text nodes: filenames and network
// names are untrusted, so nothing here may ever be parsed as HTML.
export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.flat(Infinity).filter((child) => child != null && child !== false));
  return node;
}

export function icon(name, className = "icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export function meter(percent, className = "") {
  const fill = h("div", { class: "meter-fill" });
  fill.style.width = `${Math.min(Math.max(percent, 0), 100)}%`;
  return h("div", { class: `meter ${className}` }, fill);
}

export function title(text, ...actions) {
  return h("header", { class: "title" }, h("h1", {}, text), actions);
}

export function empty(heading, text, ...extra) {
  return h("div", { class: "empty" }, h("h2", {}, heading), text ? h("p", {}, text) : null, extra);
}

/* Formatting */

export function formatBytes(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${unit && value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export const formatRate = (bytes) => (bytes == null ? "–" : `${formatBytes(bytes)}/s`);

export function formatDate(seconds) {
  return new Date(seconds * 1000).toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
}

export function formatUptime(seconds) {
  if (seconds == null) return "–";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
}

export function timeAgo(iso) {
  if (!iso || iso.startsWith("1970") || iso.startsWith("0001")) return "never";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 90) return "just now";
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 129600) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

export const encodePath = (path) => path.split("/").map(encodeURIComponent).join("/");

/* Toast */

let toastTimer;
export function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 5000);
}

/* API */

export const hooks = { unauthorized() {} };

export async function api(path, options = {}) {
  const response = await fetch(path, options);
  const isJson = (response.headers.get("content-type") || "").includes("json");
  const body = isJson ? await response.json() : null;
  if (response.status === 401) {
    hooks.unauthorized();
    throw Object.assign(new Error("Please sign in"), { status: 401 });
  }
  if (!response.ok) {
    throw Object.assign(new Error((body && body.error) || `Request failed (${response.status})`), {
      status: response.status,
    });
  }
  return body;
}

export const postJson = (path, data) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

// Asks again before anything that would stop a running upload.
export async function withUploadCheck(send, data) {
  try {
    return await send(data);
  } catch (error) {
    if (error.status !== 409 || !/upload|download/i.test(error.message)) throw error;
    const go = await confirmSheet({
      heading: "Transfer in progress",
      message: `${error.message}.`,
      action: "Continue anyway",
      danger: true,
    });
    return go ? send({ ...data, force: true }) : null;
  }
}

/* Polling. Every timer belongs to the current screen and stops when it changes. */

const timers = new Set();

export function poll(run, milliseconds) {
  timers.add(setInterval(run, milliseconds));
}

export function stopPolling() {
  timers.forEach(clearInterval);
  timers.clear();
}

/* Bottom sheets, replacing the browser's own popups */

export function closeSheet() {
  const sheet = $("#sheet");
  if (sheet.open) sheet.close();
}

export function openSheet(heading, ...content) {
  const sheet = $("#sheet");
  sheet.replaceChildren(
    h("div", { class: "sheet-handle" }),
    h("header", { class: "sheet-head" },
      h("h2", {}, heading),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Close", onclick: closeSheet }, icon("close"))),
    h("div", { class: "sheet-body" }, content));
  if (!sheet.open) sheet.showModal();
}

export function confirmSheet({ heading, message, action, danger = false }) {
  return new Promise((resolve) => {
    let answer = false;
    openSheet(heading,
      h("p", { class: "sheet-text" }, message),
      h("button", {
        class: `btn wide ${danger ? "danger-fill" : "primary"}`,
        type: "button",
        onclick: () => { answer = true; closeSheet(); },
      }, action),
      h("button", { class: "btn wide", type: "button", onclick: closeSheet }, "Cancel"));
    $("#sheet").addEventListener("close", () => resolve(answer), { once: true });
  });
}

export function promptSheet({ heading, label, action }) {
  return new Promise((resolve) => {
    let answer = null;
    const input = h("input", { type: "text", required: true, "aria-label": label, placeholder: label });
    openSheet(heading, h("form", {
      class: "sheet-form",
      onsubmit: (event) => {
        event.preventDefault();
        answer = input.value;
        closeSheet();
      },
    }, input, h("button", { class: "btn primary wide", type: "submit" }, action)));
    $("#sheet").addEventListener("close", () => resolve(answer), { once: true });
    input.focus();
  });
}

/* Status shared by all screens, and planned outages */

// A planned outage stays on screen until it's over. A "network" outage ends when the
// switch finishes, a "restart" when the Pi returns, and an "off" one never does.
export const outage = { text: "", kind: "", sawFailure: false };

function setBanner(text) {
  const banner = $("#banner");
  banner.textContent = text;
  banner.hidden = !text;
}

export function expectOutage(text, kind) {
  Object.assign(outage, { text, kind, sawFailure: false });
  setBanner(text);
}

export function clearOutage() {
  Object.assign(outage, { text: "", kind: "", sawFailure: false });
  setBanner("");
}

export async function loadStatus() {
  let status;
  try {
    status = await api("/api/status");
  } catch (error) {
    if (error.status) return null;
    outage.sawFailure = true;
    setBanner(outage.text || "Can't reach the Pi right now. Check that this device is on the same network as the Pi.");
    return null;
  }
  if (outage.sawFailure && outage.kind === "restart") clearOutage();
  else if (!outage.text) setBanner("");
  if (outage.kind === "network") await checkSwitch();
  return status;
}

async function checkSwitch() {
  const { switch: job } = await api("/api/network");
  if (!job || job.state === "switching") return;
  clearOutage();
  if (job.state === "failed") toast(`Could not join ${job.target}. The Pi went back to the previous network.`);
  else if (job.message) toast(job.message);
}
