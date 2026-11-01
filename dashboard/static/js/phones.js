// Phones: pairing and sync status.

import { showPhoneFiles } from "./files.js";
import {
  api, closeSheet, confirmSheet, empty, encodePath, formatBytes, h, icon, loadStatus, meter, openSheet, poll, postJson,
  timeAgo, title, toast,
} from "./core.js";

// Names typed for waiting phones, so a refresh doesn't lose them.
const typedNames = new Map();
let current = null;
let address = "tcp://pi5.local:22000";

function pairSheet() {
  openSheet("Add a phone",
    h("img", { class: "qr", src: "/api/phones/qr", alt: "QR code of the Pi's sync ID", width: 180, height: 180 }),
    h("ol", { class: "steps" },
      h("li", {}, "Join the same WiFi as the Pi, or the pi5-ap hotspot."),
      h("li", {}, "Install Syncthing-Fork from F-Droid on Android, or Synctrain or Möbius Sync on iPhone."),
      h("li", {}, "In the app, add a device and scan this code."),
      h("li", {}, "Approve the phone here, then share its folders with pi5 from the app. Set them to Send Only.")),
    h("code", { class: "mono-box" }, current.deviceID),
    h("p", { class: "muted small" }, `If the phone doesn't appear, set the Pi's address in the app to ${address}.`));
}

function pendingCard(device) {
  const input = h("input", {
    type: "text", maxlength: 60, required: true, value: typedNames.get(device.id) ?? device.name,
    placeholder: "Name for this phone", "aria-label": "Name for this phone",
    oninput: (event) => typedNames.set(device.id, event.target.value),
  });
  const approve = async (event) => {
    event.preventDefault();
    try {
      await postJson("/api/phones", { id: device.id, name: input.value });
      typedNames.delete(device.id);
      render(true);
    } catch (error) {
      toast(error.message);
    }
  };
  const ignore = async () => {
    try {
      await api(`/api/phones/pending/${device.id}`, { method: "DELETE" });
      render(true);
    } catch (error) {
      toast(error.message);
    }
  };
  return h("div", { class: "card notice" },
    h("h2", {}, "A phone wants to connect"),
    h("p", { class: "muted small" }, `${device.id.slice(0, 7)} from ${device.address || "unknown address"}`),
    h("form", { class: "sheet-form", onsubmit: approve },
      input,
      h("div", { class: "btn-row" },
        h("button", { class: "btn primary", type: "submit" }, "Approve"),
        h("button", { class: "btn", type: "button", onclick: ignore }, "Ignore"))));
}

function issueCard(issue) {
  return h("div", { class: "card notice" },
    h("h2", { class: "wrap" }, `Folder not accepted: ${issue.folder}`),
    h("p", { class: "muted small" },
      `${issue.phone} uses the folder ID "${issue.folderID}", which ${issue.owner} already uses. ` +
      "Syncthing would merge them, so it was refused. Remove the folder in the app and add it again with a different folder ID."));
}

function folderRow(folder) {
  const syncing = folder.state !== "idle" || folder.percent < 100;
  const state = folder.error || (syncing ? `${folder.state}, ${folder.percent}%` : "Up to date");
  return h("li", { class: "folder" },
    h("div", { class: "row-split" },
      h("strong", { class: "wrap" }, folder.label),
      h("span", { class: "muted small" }, `${folder.files} files · ${formatBytes(folder.bytes)}`)),
    h("span", { class: folder.error ? "error small" : "muted small" }, state),
    syncing ? meter(folder.percent) : null);
}

function removePhone(phone) {
  return async () => {
    const go = await confirmSheet({
      heading: `Stop syncing ${phone.name}?`,
      message: "Files already on the Pi are kept.",
      action: "Stop syncing",
      danger: true,
    });
    if (!go) return;
    try {
      await api(`/api/phones/${phone.id}`, { method: "DELETE" });
      render(true);
    } catch (error) {
      toast(error.message);
    }
  };
}

function phoneCard(phone) {
  return h("div", { class: "card" },
    h("div", { class: "row-split" },
      h("h2", { class: "wrap" }, phone.name),
      h("span", { class: "muted small nowrap" },
        h("span", { class: phone.connected ? "dot on" : "dot" }),
        phone.connected ? "Connected" : `Seen ${timeAgo(phone.lastSeen)}`)),
    phone.folders.length
      ? h("ul", { class: "folders" }, phone.folders.map(folderRow))
      : h("p", { class: "muted small" }, "No folders yet. Share a folder with pi5 from the app."),
    h("div", { class: "btn-row" },
      h("a", { class: "btn", href: `#/phones/files/${encodePath(phone.dir)}` }, "Browse files"),
      h("button", { class: "btn danger-text", type: "button", onclick: removePhone(phone) }, "Remove")));
}

let host = null;

async function render(force = false) {
  // A refresh must not disturb a name being typed.
  if (!force && document.activeElement?.tagName === "INPUT" && host?.contains(document.activeElement)) return;
  let data;
  try {
    data = await api("/api/phones");
  } catch (error) {
    host.replaceChildren(empty("Can't load phones", error.message));
    return;
  }
  if (!data.online) {
    host.replaceChildren(empty("Sync service is offline", "It normally restarts by itself. Try again in a minute."));
    return;
  }
  current = data;
  const pair = h("button", { class: "btn primary wide", type: "button", onclick: pairSheet }, icon("plus"), "Add a phone");
  host.replaceChildren(...[
    ...data.pending.map(pendingCard),
    ...data.issues.map(issueCard),
    ...data.phones.map(phoneCard),
    data.phones.length || data.pending.length ? null : empty("No phones yet", "Pair a phone and its files sync to the Pi, each phone in its own folder."),
    pair,
  ].filter(Boolean));
}

export function show(parts) {
  if (parts[0] === "files") return showPhoneFiles(parts.slice(1));
  host = h("div", {});
  render(true);
  loadStatus().then((status) => {
    if (status?.network.ips[0]) address = `tcp://${status.network.ips[0]}:22000`;
  });
  poll(render, 5000);
  return [title("Phones"), host];
}
