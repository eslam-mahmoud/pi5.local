// Tech: raw detail for technical users. Everything here is read-only.

import { $, api, empty, formatBytes, formatRate, formatUptime, h, meter, openSheet, poll, title, toast } from "./core.js";

const SECTIONS = {
  overview: "Overview",
  processes: "Processes",
  services: "Services",
  gpio: "GPIO",
  devices: "Devices",
  logs: "Logs",
};

let body = null;
let section = "overview";
let lastServices = "";
let serviceFilter = "";
let logSource = "pi5-dashboard";
const openGroups = new Set(["failed", "running"]);

const percentOf = (part, whole) => (whole ? (part / whole) * 100 : 0);
const kv = (label, value) => h("div", { class: "kv" }, h("span", {}, label), h("span", { class: "mono" }, value ?? "–"));
const card = (heading, ...content) => h("section", { class: "card tech-card" }, h("h2", {}, heading), content);

function usage(label, used, total, text) {
  return h("div", { class: "usage" },
    h("div", { class: "row-split small" }, h("span", {}, label), h("span", { class: "mono" }, text)),
    meter(percentOf(used, total), percentOf(used, total) > 90 ? "low" : ""));
}

/* Overview */

function systemCard({ system, videocore }) {
  return card("System",
    kv("Hostname", system.hostname),
    kv("Model", system.model),
    kv("OS", system.os),
    kv("Kernel", system.kernel),
    kv("Arch", system.arch),
    kv("Firmware", videocore.firmware.trim()),
    kv("Uptime", formatUptime(system.uptime)));
}

function cpuCard({ cpu }) {
  return card("CPU",
    usage("Total", cpu.percent ?? 0, 100, `${cpu.percent ?? 0}%`),
    cpu.cores.map((core, index) => usage(`Core ${index}`, core.percent, 100, `${core.percent}% · ${core.mhz} MHz`)),
    kv("Governor", cpu.governor),
    kv("Load 1 / 5 / 15", cpu.load.map((value) => value.toFixed(2)).join(" / ")));
}

function powerCard({ videocore }) {
  const { throttled } = videocore;
  const now = throttled?.now ?? [];
  const since = throttled?.sinceBoot ?? [];
  return card("Clocks and power",
    kv("ARM", videocore.armMhz && `${videocore.armMhz} MHz`),
    kv("Core", videocore.coreMhz && `${videocore.coreMhz} MHz`),
    kv("V3D (GPU)", videocore.v3dMhz && `${videocore.v3dMhz} MHz`),
    kv("Core voltage", videocore.coreVolts && `${videocore.coreVolts.toFixed(4)} V`),
    kv("RAM voltage", videocore.ramVolts && `${videocore.ramVolts.toFixed(4)} V`),
    kv("Supply (5V rail)", videocore.supplyVolts && `${videocore.supplyVolts.toFixed(3)} V`),
    kv("Throttle flags", throttled?.raw),
    h("p", { class: now.length ? "error small" : "muted small" }, now.length ? `Now: ${now.join(", ")}` : "No throttling now"),
    h("p", { class: since.length ? "error small" : "muted small" }, since.length ? `Since boot: ${since.join(", ")}` : "No throttling since boot"));
}

function thermalCard({ temperatures, videocore, fan }) {
  const readings = { ...temperatures };
  if (videocore.pmicTemp != null) readings["Power chip"] = videocore.pmicTemp;
  return card("Temperature and fan",
    Object.entries(readings).map(([label, value]) => kv(label, `${value} °C`)),
    fan ? kv("Fan speed", `${fan.rpm ?? "–"} RPM`) : kv("Fan", "Not fitted"),
    fan?.percent != null ? usage("Fan drive", fan.percent, 100, `${fan.percent}%`) : null);
}

function memoryCard({ memory }) {
  return card("Memory",
    usage("RAM", memory.used, memory.total, `${formatBytes(memory.used)} / ${formatBytes(memory.total)}`),
    usage("Swap", memory.swapUsed, memory.swapTotal, `${formatBytes(memory.swapUsed)} / ${formatBytes(memory.swapTotal)}`),
    kv("Page cache", formatBytes(memory.cached)),
    kv("Buffers", formatBytes(memory.buffers)));
}

function diskCard({ disks, diskIo }) {
  return card("Disks",
    disks.map((disk) => usage(`${disk.mount} (${disk.fstype})`, disk.used, disk.total, `${formatBytes(disk.used)} / ${formatBytes(disk.total)}`)),
    diskIo.map((device) => kv(`${device.device} read / write`, `${formatRate(device.read)} / ${formatRate(device.write)}`)));
}

function networkCard({ network }) {
  return card("Interfaces", network.map((link) => h("div", { class: "subcard" },
    h("div", { class: "row-split" },
      h("strong", { class: "mono" }, link.name),
      h("span", { class: "small muted" }, h("span", { class: link.state === "up" ? "dot on" : "dot" }), link.state)),
    link.ips.map((ip) => kv("Address", ip)),
    kv("MAC", link.mac),
    kv("Rate down / up", `${formatRate(link.rx)} / ${formatRate(link.tx)}`),
    kv("Total down / up", `${formatBytes(link.rxTotal)} / ${formatBytes(link.txTotal)}`))));
}

function renderOverview(data) {
  body.replaceChildren(h("div", { class: "tech-grid" },
    systemCard(data), cpuCard(data), powerCard(data), thermalCard(data), memoryCard(data), diskCard(data), networkCard(data)));
}

/* Processes */

function renderProcesses({ processes }) {
  body.replaceChildren(h("section", { class: "card tech-card" },
    h("h2", {}, `Top ${processes.top.length} of ${processes.total}`),
    h("table", { class: "table" },
      h("thead", {}, h("tr", {},
        h("th", {}, "Process"), h("th", { class: "num" }, "CPU"), h("th", { class: "num" }, "RAM"))),
      h("tbody", {}, processes.top.map((row) => h("tr", {},
        h("td", {},
          h("span", { class: "mono wrap" }, row.name),
          h("span", { class: "row-note" }, `${row.pid} · ${row.user} · ${row.state} · ${row.threads} threads`)),
        h("td", { class: "num mono" }, `${row.cpu}%`),
        h("td", { class: "num mono" }, formatBytes(row.rss))))))));
}

/* Services */

function serviceGroup(name, label, units) {
  const matches = units.filter((unit) => `${unit.unit} ${unit.description}`.toLowerCase().includes(serviceFilter));
  const group = h("details", {
    class: `card service-group ${name}`,
    open: openGroups.has(name) || (serviceFilter && matches.length > 0),
    ontoggle: (event) => {
      if (event.target.open) openGroups.add(name);
      else openGroups.delete(name);
    },
  },
  h("summary", {}, `${label} (${matches.length})`),
  matches.map((unit) => h("div", { class: "service" },
    h("div", { class: "row-split" }, h("span", { class: "mono wrap" }, unit.unit), h("span", { class: "small muted" }, unit.state)),
    h("span", { class: "row-note" }, unit.description))));
  return group;
}

function drawServices(services, list) {
  list.replaceChildren(
    serviceGroup("failed", "Failed", services.failed),
    serviceGroup("running", "Running", services.running),
    serviceGroup("inactive", "Inactive", services.inactive));
}

function renderServices({ services }) {
  const fingerprint = JSON.stringify(services);
  if (fingerprint === lastServices && body.querySelector(".service-list")) return;
  lastServices = fingerprint;
  const list = h("div", { class: "service-list" });
  body.replaceChildren(
    h("input", {
      type: "search",
      placeholder: "Filter services",
      "aria-label": "Filter services",
      value: serviceFilter,
      oninput: (event) => {
        serviceFilter = event.target.value.toLowerCase();
        drawServices(services, list);
      },
    }),
    list);
  drawServices(services, list);
}

/* GPIO */

const PIN_LABELS = { "3v3": "3.3V", "5v": "5V", gnd: "GND" };
const MODE_LABELS = { no: "Unused", ip: "Input", op: "Output" };

const modeLabel = (mode) => MODE_LABELS[mode] ?? `Alt function ${mode.replace("a", "")}`;

function pinClass(pin) {
  if (pin.kind !== "gpio") return `pin-${pin.kind}`;
  if (pin.mode === "no") return "pin-idle";
  if (pin.mode === "ip" || pin.mode === "op") return `pin-${pin.mode}`;
  return "pin-alt";
}

function pinDetails(pin) {
  if (pin.kind !== "gpio") {
    openSheet(`Pin ${pin.pin}`, h("p", { class: "sheet-text" }, `Power rail: ${PIN_LABELS[pin.kind] ?? pin.kind}`));
    return;
  }
  openSheet(`Pin ${pin.pin}: ${pin.name}`,
    kv("Mode", modeLabel(pin.mode)),
    kv("Pull", { pu: "Up", pd: "Down", pn: "None" }[pin.pull] ?? (pin.pull || "–")),
    kv("Level", { hi: "High", lo: "Low" }[pin.level] ?? "–"),
    kv("Function", pin.function));
}

function pinButton(pin, side) {
  const label = pin.kind === "gpio" ? pin.name.replace("GPIO", "") : PIN_LABELS[pin.kind] ?? pin.kind;
  return h("button", {
    class: `pin ${side} ${pinClass(pin)}`,
    type: "button",
    "aria-label": `Pin ${pin.pin}: ${pin.kind === "gpio" ? `${pin.name}, ${modeLabel(pin.mode)}` : label}`,
    onclick: () => pinDetails(pin),
  },
  h("span", { class: "pin-name mono" }, pin.kind === "gpio" ? `GPIO ${label}` : label),
  h("span", { class: "pin-dot mono" }, pin.pin));
}

async function renderGpio() {
  let data;
  try {
    data = await api("/api/tech/gpio");
  } catch (error) {
    body.replaceChildren(empty("Can't read GPIO", error.message));
    return;
  }
  if (section !== "gpio") return;
  const rows = [];
  for (let index = 0; index + 1 < data.pins.length; index += 2) {
    rows.push(h("div", { class: "pin-row" }, pinButton(data.pins[index], "left"), pinButton(data.pins[index + 1], "right")));
  }
  body.replaceChildren(h("section", { class: "card tech-card" },
    h("h2", {}, "40-pin header"),
    h("p", { class: "muted small" }, "Read-only. Tap a pin for details. Pin 1 is at the end of the header farthest from the USB ports."),
    h("div", { class: "pin-legend small" }, [["pin-3v3", "3.3V"], ["pin-5v", "5V"], ["pin-gnd", "GND"], ["pin-ip", "Input"], ["pin-op", "Output"], ["pin-alt", "Alt"], ["pin-idle", "Unused"]]
      .map(([kind, label]) => h("span", {}, h("i", { class: `swatch ${kind}` }), label))),
    h("div", { class: "pins" }, rows)));
}

/* Devices */

function renderDevices({ usb, docker }) {
  const devices = usb.filter((device) => !device.rootHub);
  const dockerNote = {
    missing: "Docker isn't installed.",
    denied: "Docker is installed, but the dashboard isn't allowed to read it.",
    ok: "No containers running.",
  }[docker.state];
  body.replaceChildren(h("div", { class: "tech-grid" },
    card("USB",
      devices.length ? devices.map((device) => h("div", { class: "subcard" },
        h("strong", { class: "wrap" }, device.product || "Unknown device"),
        kv("Maker", device.maker),
        kv("ID", device.id),
        kv("Port", device.port),
        kv("Speed", device.mbps && `${device.mbps} Mbit/s`)))
        : h("p", { class: "muted small" }, "Nothing plugged in."),
      h("p", { class: "muted small" }, `${usb.length - devices.length} root hubs not shown.`)),
    card("Docker", docker.containers.length ? docker.containers.map((container) => h("div", { class: "subcard" },
      h("strong", { class: "mono wrap" }, container.name),
      kv("Image", container.image),
      kv("Status", container.status)))
      : h("p", { class: "muted small" }, dockerNote))));
}

/* Logs */

async function renderLogs(sources) {
  const output = h("pre", { class: "log", tabindex: 0 }, "Loading…");
  const load = async () => {
    try {
      const { lines } = await api(`/api/tech/logs?source=${encodeURIComponent(logSource)}`);
      output.textContent = lines.join("\n");
      output.scrollTop = output.scrollHeight;
    } catch (error) {
      output.textContent = error.message;
    }
  };
  const chips = h("div", { class: "chips" }, sources.map((source) => h("button", {
    class: source === logSource ? "chip active" : "chip",
    type: "button",
    onclick: () => {
      logSource = source;
      renderLogs(sources);
    },
  }, source)));
  body.replaceChildren(chips, h("button", { class: "btn wide", type: "button", onclick: load }, "Refresh"), output);
  load();
}

/* Screen */

async function refresh() {
  if (section === "gpio") {
    renderGpio();
    return;
  }
  let data;
  try {
    data = await api("/api/tech");
  } catch (error) {
    if (error.status && error.status !== 401) toast(error.message);
    return;
  }
  if (!body?.isConnected) return;
  if (section === "overview") renderOverview(data);
  else if (section === "processes") renderProcesses(data);
  else if (section === "services") renderServices(data);
  else if (section === "devices") renderDevices(data);
  else if (section === "logs" && !$("#view .log")) renderLogs(data.logs);
}

export function show(parts) {
  section = SECTIONS[parts[0]] ? parts[0] : "overview";
  body = h("div", {}, h("p", { class: "muted" }, "Loading…"));
  lastServices = "";
  setTimeout(refresh);
  if (section !== "logs") poll(refresh, 5000);
  return [
    title("Tech"),
    h("nav", { class: "chips sticky", "aria-label": "Technical sections" }, Object.entries(SECTIONS).map(([key, label]) => h("a", {
      class: key === section ? "chip active" : "chip",
      href: `#/tech/${key}`,
      "aria-current": key === section ? "page" : null,
    }, label))),
    body,
  ];
}
