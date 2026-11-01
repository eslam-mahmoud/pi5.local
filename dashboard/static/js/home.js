// Home: everyday status, network switching and power actions.

import {
  api, closeSheet, confirmSheet, expectOutage, formatBytes, formatRate, formatUptime, h,
  hooks, icon, loadStatus, meter, openSheet, poll, postJson, title, toast, withUploadCheck,
} from "./core.js";

let root = {};
let phoneSync = { online: false };

function signalWord(dbm) {
  if (dbm == null) return "";
  if (dbm >= -55) return "Excellent signal";
  if (dbm >= -67) return "Good signal";
  if (dbm >= -75) return "Fair signal";
  return "Weak signal";
}

function tile(label, value, note, percent, bad = false) {
  return h("div", { class: bad ? "tile bad" : "tile" },
    h("span", { class: "tile-label" }, label),
    h("span", { class: "tile-value" }, value),
    percent == null ? null : meter(percent),
    note ? h("span", { class: "tile-note" }, note) : null);
}

function listRow(label, value, bad = false) {
  return h("li", { class: bad ? "row bad" : "row" },
    h("span", { class: "row-main" }, label),
    h("span", { class: "row-value" }, value));
}

function renderHero({ network }) {
  const onHotspot = network.mode === "hotspot";
  const name = network.mode === "offline" ? "Not connected" : network.name;
  const kind = { wifi: "WiFi", hotspot: "Hotspot", ethernet: "Ethernet", offline: "No network" }[network.mode];
  const details = [kind, network.ips[0]];
  if (network.clients != null) details.push(`${network.clients} connected`);
  root.hero.className = `hero ${network.mode}`;
  root.hero.replaceChildren(
    h("span", { class: "hero-icon" }, icon(onHotspot ? "hotspot" : "wifi")),
    h("span", { class: "hero-text" },
      h("strong", {}, name),
      h("span", {}, details.filter(Boolean).join(" · ")),
      network.signal != null ? h("span", {}, signalWord(network.signal)) : null),
    icon("chevron", "chevron"));
}

function renderStatus(status) {
  const { cpu, memory, disk, traffic, services, uploads } = status;
  const hot = status.temperature != null && status.temperature >= 80;
  const stopped = services.filter((service) => !service.active).map((service) => service.label);
  const onHotspot = status.network.mode === "hotspot";
  const problems = stopped.filter((label) => !(onHotspot && label === "WiFi fallback"));

  renderHero(status);
  root.uploads.replaceChildren(...uploads.map((upload) => {
    const percent = (upload.received / upload.total) * 100;
    return h("div", { class: "card" },
      h("div", { class: "row-split" },
        h("strong", { class: "clip" }, upload.name),
        h("span", { class: "muted" }, `${Math.round(percent)}%`)),
      meter(percent),
      h("p", { class: "muted small" }, `Uploading. Keep the Pi powered on. ${formatBytes(upload.received)} of ${formatBytes(upload.total)}`));
  }));
  root.downloads.replaceChildren(...status.downloads.map((download) => h("a", { class: "card link", href: "#/downloads" },
    h("div", { class: "row-split" },
      h("strong", { class: "clip" }, download.name),
      h("span", { class: "muted" }, `${Math.round(download.percent)}%`)),
    meter(download.percent),
    h("p", { class: "muted small" }, "Downloading to the Pi."))));
  root.tiles.replaceChildren(
    tile("CPU", cpu.percent == null ? "–" : `${cpu.percent}%`, "", cpu.percent ?? 0),
    tile("Memory", memory ? `${Math.round((memory.used / memory.total) * 100)}%` : "–",
      "", memory ? (memory.used / memory.total) * 100 : 0),
    tile("Temperature", status.temperature == null ? "–" : `${status.temperature}°C`, hot ? "Running hot" : "", null, hot),
    tile("Storage", `${formatBytes(disk.free)}`, "free", (disk.used / disk.total) * 100, disk.free / disk.total < 0.1));
  root.details.replaceChildren(
    listRow("Download", formatRate(traffic.rx)),
    listRow("Upload", formatRate(traffic.tx)),
    listRow("Uptime", formatUptime(status.uptime)),
    listRow("Power", status.underVoltage ? "Supply too weak" : status.underVoltage == null ? "–" : "OK", Boolean(status.underVoltage)),
    listRow("Services", problems.length ? `Stopped: ${problems.join(", ")}` : "All running", problems.length > 0));
}

async function refresh() {
  const status = await loadStatus();
  if (status) renderStatus(status);
}

/* Network */

function reconnectHelp(network) {
  return network.hotspot
    ? `Switching the Pi to the ${network.label} hotspot. Join ${network.label} on this device, then open http://10.42.0.1`
    : `Switching the Pi to ${network.label}. Join ${network.label} on this device, then open http://pi5.local`;
}

async function switchNetwork(path, data, network) {
  try {
    const sent = await withUploadCheck((body) => postJson(path, body), data);
    if (sent) expectOutage(reconnectHelp(network), "network");
  } catch (error) {
    toast(error.message);
  }
}

async function connectSaved(network) {
  const go = await confirmSheet({
    heading: `Move to ${network.ssid}?`,
    message: "If the Pi can't connect, it goes back to the current network by itself.",
    action: "Connect",
  });
  if (go) switchNetwork("/api/network/connect", { name: network.name }, { label: network.ssid });
}

async function startHotspot(ssid) {
  const go = await confirmSheet({
    heading: `Start ${ssid}?`,
    message: "The Pi leaves WiFi and stays on the hotspot until you switch back or restart it. It has no internet while the hotspot is on.",
    action: "Start hotspot",
  });
  if (go) switchNetwork("/api/network/connect", { name: "Hotspot" }, { label: ssid, hotspot: true });
}

function askPassword(network) {
  const password = h("input", {
    type: "password", autocomplete: "off", minlength: 8, maxlength: 63, required: true,
    placeholder: "WiFi password", "aria-label": "WiFi password",
  });
  openSheet(`Join ${network.ssid}`, h("form", {
    class: "sheet-form",
    onsubmit: (event) => {
      event.preventDefault();
      closeSheet();
      switchNetwork("/api/network/add", { ssid: network.ssid, password: password.value }, { label: network.ssid });
    },
  }, password, h("p", { class: "muted small" }, "If it doesn't work, the Pi goes back to the current network and forgets this one."),
  h("button", { class: "btn primary wide", type: "submit" }, "Join")));
  password.focus();
}

async function addOpen(network) {
  const go = await confirmSheet({
    heading: `Join ${network.ssid}?`,
    message: "This network has no password, so anyone nearby can see its traffic.",
    action: "Join",
  });
  if (go) switchNetwork("/api/network/add", { ssid: network.ssid }, { label: network.ssid });
}

async function lookAround(ssid) {
  const go = await confirmSheet({
    heading: "Look for networks?",
    message: `The Pi can't search while it's a hotspot, so ${ssid} turns off for about 20 seconds and this device disconnects.`,
    action: "Look for networks",
  });
  if (!go) return;
  try {
    const sent = await withUploadCheck((body) => postJson("/api/network/scan", body), {});
    if (sent) expectOutage(`Looking for networks. ${ssid} is off for about 20 seconds. Rejoin it if this device doesn't reconnect by itself.`, "network");
  } catch (error) {
    toast(error.message);
  }
}

function networkRow(label, signal, note, onTap) {
  const body = [
    h("span", { class: "row-main clip" }, label),
    signal == null ? null : h("span", { class: "row-value" }, `${signal}%`),
    note ? h("span", { class: "row-value" }, note) : null,
  ];
  return h("li", {}, onTap
    ? h("button", { class: "row tap", type: "button", onclick: onTap }, body, icon("chevron", "chevron"))
    : h("div", { class: "row" }, body));
}

async function openNetworks(scanNote = "") {
  let data;
  try {
    data = await api("/api/network");
  } catch (error) {
    toast(error.message);
    return;
  }
  const { hotspot, saved, available } = data;
  const busy = data.switch?.state === "switching";
  const rows = [];
  if (hotspot.ssid) {
    rows.push(networkRow(`${hotspot.ssid} hotspot`, null, hotspot.active ? "On" : "",
      hotspot.active || busy ? null : () => startHotspot(hotspot.ssid)));
  }
  for (const network of saved) {
    rows.push(networkRow(network.ssid, network.signal, network.active ? "Connected" : "",
      network.active || busy ? null : () => connectSaved(network)));
  }
  const others = available.map((network) => networkRow(network.ssid, network.signal, "",
    busy ? null : () => (network.open ? addOpen(network) : askPassword(network))));

  const scan = h("button", {
    class: "btn wide",
    type: "button",
    disabled: busy,
    onclick: async () => {
      if (hotspot.active) return lookAround(hotspot.ssid);
      scan.disabled = true;
      scan.textContent = "Scanning…";
      let failure = "";
      try {
        await postJson("/api/network/scan", {});
      } catch (error) {
        failure = error.message;
      }
      return openNetworks(failure);
    },
  }, "Scan for networks");

  const busyText = data.switch?.scanning ? "Looking for networks…" : `Switching to ${data.switch?.target}…`;
  openSheet("Network",
    busy ? h("p", { class: "sheet-text" }, busyText) : null,
    hotspot.active ? h("p", { class: "sheet-text" }, "Staying on the hotspot until you connect to WiFi or restart.") : null,
    h("ul", { class: "group" }, rows),
    others.length ? h("h3", { class: "group-title" }, "Nearby") : null,
    others.length ? h("ul", { class: "group" }, others) : null,
    scanNote ? h("p", { class: "sheet-text" }, scanNote) : null,
    !scanNote && !others.length && !hotspot.active ? h("p", { class: "sheet-text" }, "No other networks in range.") : null,
    scan);
}

/* Actions */

async function runAction(data, after) {
  const send = (body) => postJson("/api/system/action", body);
  try {
    const sent = await withUploadCheck(send, data);
    if (sent) after();
  } catch (error) {
    // The Pi can go down before it finishes replying.
    if (error.status) toast(error.message);
    else after();
  }
}

async function restartPi() {
  const go = await confirmSheet({
    heading: "Restart the Pi?",
    message: "It's back in about a minute.",
    action: "Restart",
  });
  if (go) {
    runAction({ action: "reboot" },
      () => expectOutage("The Pi is restarting. This page comes back by itself in about a minute.", "restart"));
  }
}

async function shutDownPi() {
  const go = await confirmSheet({
    heading: "Shut down the Pi?",
    message: "To start it again, press its power button or unplug and replug the power.",
    action: "Shut down",
    danger: true,
  });
  if (go) {
    runAction({ action: "poweroff" },
      () => expectOutage("The Pi is shutting down. Wait 20 seconds before unplugging it. To start it again, press its power button or replug the power.", "off"));
  }
}

async function loadPhoneSync() {
  try {
    phoneSync = await api("/api/phones");
  } catch {
    phoneSync = { online: false };
  }
}

async function openSync() {
  await loadPhoneSync();
  const paired = phoneSync.online ? phoneSync.phones.length : 0;
  const connected = paired ? phoneSync.phones.filter((phone) => phone.connected).length : 0;
  openSheet("Phone sync",
    h("p", { class: "sheet-text" }, !phoneSync.online ? "The sync service isn't running."
      : paired ? `${connected} of ${paired} phones connected${phoneSync.paused ? ", paused" : ""}.` : "No phones paired yet."),
    paired ? h("button", {
      class: "btn wide",
      type: "button",
      onclick: async () => {
        try {
          await postJson("/api/phones/pause", { paused: !phoneSync.paused });
          closeSheet();
          toast(phoneSync.paused ? "Phone sync resumed" : "Phone sync paused");
        } catch (error) {
          toast(error.message);
        }
      },
    }, phoneSync.paused ? "Resume sync" : "Pause sync") : null,
    h("button", {
      class: "btn wide",
      type: "button",
      onclick: () => {
        closeSheet();
        runAction({ action: "restart-sync" }, () => toast("Phone sync restarted"));
      },
    }, "Restart sync service"),
    h("a", { class: "btn wide", href: "#/phones" }, "Open Phones"));
}

function quick(symbol, label, onclick, className = "") {
  return h("button", { class: `quick ${className}`, type: "button", onclick },
    h("span", { class: "quick-icon" }, icon(symbol)), h("span", {}, label));
}

export function show() {
  root = {
    hero: h("button", { class: "hero", type: "button", onclick: () => openNetworks() }),
    uploads: h("div", {}),
    downloads: h("div", {}),
    tiles: h("div", { class: "tiles" }),
    details: h("ul", { class: "group" }),
  };
  refresh();
  poll(refresh, 4000);

  return [
    title("pi5"),
    root.hero,
    root.uploads,
    root.downloads,
    root.tiles,
    h("div", { class: "quick-row" },
      quick("wifi", "Network", () => openNetworks()),
      quick("sync", "Sync", openSync),
      quick("restart", "Restart", restartPi),
      quick("power", "Shut down", shutDownPi, "danger")),
    root.details,
    h("ul", { class: "group" },
      h("li", {}, h("a", { class: "row tap", href: "#/tech" },
        h("span", { class: "row-main" }, "Technical details"), icon("chevron", "chevron"))),
      h("li", {}, h("button", { class: "row tap", type: "button", onclick: () => hooks.signOut() },
        h("span", { class: "row-main" }, "Sign out")))),
  ];
}
