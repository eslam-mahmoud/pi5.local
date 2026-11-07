// Files: preset storage, and the read-only browser for phone backups.

import {
  api, closeSheet, confirmSheet, empty, encodePath, formatBytes, formatDate, h, icon, loadStatus, meter,
  openSheet, postJson, promptSheet, title, toast,
} from "./core.js";

export function crumbs(baseHash, rootLabel, path) {
  const parts = path ? path.split("/") : [];
  if (!parts.length) return null;
  const links = [h("a", { href: baseHash }, rootLabel)];
  parts.forEach((part, index) => {
    const target = parts.slice(0, index + 1).join("/");
    links.push(h("span", {}, "/"), h("a", { href: `${baseHash}/${encodePath(target)}` }, part));
  });
  return h("nav", { class: "crumbs", "aria-label": "Folder path" }, links);
}

function deleteEntry(root, full, entry, reload) {
  return async () => {
    const go = await confirmSheet({
      heading: `Delete ${entry.name}?`,
      message: entry.dir ? "The folder and everything in it are removed. This can't be undone." : "This can't be undone.",
      action: "Delete",
      danger: true,
    });
    if (!go) return;
    try {
      await api(`/api/files/${root}?path=${encodeURIComponent(full)}`, { method: "DELETE" });
      reload();
    } catch (error) {
      toast(error.message);
    }
  };
}

function entrySheet(root, full, entry, writable, reload) {
  openSheet(entry.name,
    h("p", { class: "sheet-text" }, entry.dir ? "Folder" : `${formatBytes(entry.size)} · ${formatDate(entry.mtime)}`),
    entry.dir ? null : h("a", {
      class: "btn primary wide",
      href: `/files/${root}/${encodePath(full)}`,
      download: entry.name,
      onclick: closeSheet,
    }, "Download"),
    writable ? h("button", { class: "btn wide danger-text", type: "button", onclick: deleteEntry(root, full, entry, reload) }, "Delete") : null);
}

export function fileRow(root, baseHash, path, entry, writable, reload) {
  const full = path ? `${path}/${entry.name}` : entry.name;
  const meta = entry.dir ? "Folder" : `${formatBytes(entry.size)} · ${formatDate(entry.mtime)}`;
  const body = [
    icon(entry.dir ? "folder" : "file", "row-icon"),
    h("span", { class: "row-main" }, h("span", { class: "wrap" }, entry.name), h("span", { class: "row-note" }, meta)),
  ];
  const openDetails = () => entrySheet(root, full, entry, writable, reload);
  return h("li", { class: "file-row" },
    entry.dir
      ? h("a", { class: "row tap", href: `${baseHash}/${encodePath(full)}` }, body)
      : h("button", { class: "row tap", type: "button", onclick: openDetails }, body),
    entry.dir && writable
      ? h("button", { class: "icon-btn", type: "button", "aria-label": `Options for ${entry.name}`, onclick: openDetails }, icon("more"))
      : null);
}

// Resolves with an error message, or "" when the file arrived.
function uploadFile(file, path, list, label) {
  const percent = h("span", { class: "muted" }, "0%");
  const bar = meter(0);
  const row = h("div", { class: "card" },
    h("div", { class: "row-split" }, h("strong", { class: "clip" }, label), percent), bar);
  list.append(row);

  return new Promise((resolve) => {
    const url = `/api/files/presets?path=${encodeURIComponent(path)}&name=${encodeURIComponent(file.name)}`;
    const request = new XMLHttpRequest();
    request.open("PUT", url);
    request.upload.onprogress = (event) => {
      const done = Math.round((event.loaded / event.total) * 100);
      percent.textContent = `${done}%`;
      bar.firstChild.style.width = `${done}%`;
    };
    const finish = (message = "") => {
      row.remove();
      resolve(message);
    };
    request.onload = () => {
      if (request.status < 300) return finish();
      let message = `upload failed (${request.status})`;
      try { message = JSON.parse(request.responseText).error; } catch { /* keep default */ }
      return finish(message);
    };
    request.onerror = () => finish("connection lost");
    request.send(file);
  });
}

const isHidden = (name) => name.startsWith(".");

// Creates each missing folder on the way to `folder`, inside `base`.
async function makeFolders(base, folder, made) {
  let current = base;
  for (const name of folder.split("/").filter(Boolean)) {
    const next = current ? `${current}/${name}` : name;
    if (!made.has(next)) {
      try {
        await postJson("/api/files/presets/folders", { path: current, name });
      } catch (error) {
        if (error.status !== 409) throw error; // 409: it's already there
      }
      made.add(next);
    }
    current = next;
  }
}

// Every file inside a dropped folder, with its path relative to the drop.
async function readEntry(entry, folder, found) {
  if (isHidden(entry.name)) return;
  if (entry.isFile) {
    found.push({ file: await new Promise((resolve, reject) => entry.file(resolve, reject)), folder });
    return;
  }
  const reader = entry.createReader();
  const inside = folder ? `${folder}/${entry.name}` : entry.name;
  for (;;) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) return;
    for (const child of batch) await readEntry(child, inside, found);
  }
}

async function droppedItems(dataTransfer) {
  const entries = [...dataTransfer.items].map((item) => item.webkitGetAsEntry?.()).filter(Boolean);
  if (!entries.length) return [...dataTransfer.files].map((file) => ({ file, folder: "" }));
  const found = [];
  for (const entry of entries) await readEntry(entry, "", found);
  return found;
}

function pickedItems(files) {
  return [...files]
    .map((file) => ({ file, folder: (file.webkitRelativePath || "").split("/").slice(0, -1).join("/") }))
    .filter(({ file, folder }) => !isHidden(file.name) && !folder.split("/").some(isHidden));
}

async function renderPresets(host, uploads, path) {
  const baseHash = "#/files";
  const reload = () => renderPresets(host, uploads, path);
  let data;
  try {
    data = await api(`/api/files/presets?path=${encodeURIComponent(path)}`);
  } catch (error) {
    host.replaceChildren(empty("Can't open this folder", error.message));
    return;
  }

  // Files already in a folder, by name and size, so uploading a folder again only sends
  // what's missing, for example after an interrupted upload.
  const listings = new Map();
  const sizesIn = async (folderPath) => {
    if (!listings.has(folderPath)) {
      let sizes = new Map();
      try {
        const data = await api(`/api/files/presets?path=${encodeURIComponent(folderPath)}`);
        sizes = new Map(data.entries.filter((entry) => !entry.dir).map((entry) => [entry.name, entry.size]));
      } catch { /* a new folder has nothing in it */ }
      listings.set(folderPath, sizes);
    }
    return listings.get(folderPath);
  };

  const addItems = async (items) => {
    const made = new Set();
    const failed = [];
    let skipped = 0;
    for (const { file, folder } of items) {
      const label = folder ? `${folder}/${file.name}` : file.name;
      const target = [path, folder].filter(Boolean).join("/");
      try {
        await makeFolders(path, folder, made);
      } catch (error) {
        failed.push(`${label}: ${error.message}`);
        continue;
      }
      if (folder && (await sizesIn(target)).get(file.name) === file.size) {
        skipped += 1;
        continue;
      }
      const error = await uploadFile(file, target, uploads, label);
      if (error) failed.push(`${label}: ${error}`);
    }
    reload();
    showDisk(host.parentElement.querySelector(".disk"));
    const notes = [];
    if (failed.length === 1) notes.push(failed[0]);
    else if (failed.length) notes.push(`${failed.length} files weren't uploaded. ${failed[0]}`);
    if (skipped) notes.push(`Skipped ${skipped} ${skipped === 1 ? "file" : "files"} already on the Pi.`);
    if (notes.length) toast(notes.join(" "));
  };
  const picker = h("input", {
    type: "file", multiple: true, hidden: true,
    onchange: (event) => addItems(pickedItems(event.target.files)),
  });
  const folderPicker = h("input", {
    type: "file", multiple: true, hidden: true, webkitdirectory: true,
    onchange: (event) => addItems(pickedItems(event.target.files)),
  });
  const newFolder = async () => {
    const name = await promptSheet({ heading: "New folder", label: "Folder name", action: "Create" });
    if (!name) return;
    try {
      await postJson("/api/files/presets/folders", { path, name });
      reload();
    } catch (error) {
      toast(error.message);
    }
  };
  const addMenu = () => openSheet("Add to Files",
    h("button", { class: "btn primary wide", type: "button", onclick: () => { closeSheet(); picker.click(); } }, "Upload files"),
    h("button", { class: "btn wide", type: "button", onclick: () => { closeSheet(); folderPicker.click(); } }, "Upload a folder"),
    h("button", { class: "btn wide", type: "button", onclick: () => { closeSheet(); newFolder(); } }, "New folder"));

  const rows = data.entries.map((entry) => fileRow("presets", baseHash, path, entry, true, reload));
  const list = rows.length
    ? h("ul", { class: "group" }, rows)
    : empty(path ? "This folder is empty" : "No preset files yet", "Tap + to upload files.");

  const drop = h("div", {
    class: "drop",
    ondragover: (event) => { event.preventDefault(); drop.classList.add("dropping"); },
    ondragleave: () => drop.classList.remove("dropping"),
    ondrop: async (event) => {
      event.preventDefault();
      drop.classList.remove("dropping");
      addItems(await droppedItems(event.dataTransfer));
    },
  }, list);

  host.replaceChildren(
    crumbs(baseHash, "Files", path) ?? "",
    drop,
    picker,
    folderPicker,
    h("button", { class: "fab", type: "button", "aria-label": "Add files or a folder", onclick: addMenu }, icon("plus")));
}

export async function showDisk(node) {
  const status = await loadStatus();
  if (!status || !node) return;
  const { disk } = status;
  node.replaceChildren(
    h("div", { class: "row-split muted small" }, h("span", {}, "Pi storage"), h("span", {}, `${formatBytes(disk.free)} free of ${formatBytes(disk.total)}`)),
    meter((disk.used / disk.total) * 100, disk.free / disk.total < 0.1 ? "low" : ""));
}

export function fileTabs(active) {
  return h("nav", { class: "chips", "aria-label": "File areas" },
    [["#/files", "Presets"], ["#/downloads", "Downloads"]].map(([href, label]) => h("a", {
      class: label === active ? "chip active" : "chip",
      href,
      "aria-current": label === active ? "page" : null,
    }, label)));
}

export function show(parts) {
  const path = parts.join("/");
  const disk = h("div", { class: "disk" });
  const uploads = h("div", {});
  const host = h("div", {});
  showDisk(disk);
  renderPresets(host, uploads, path);
  return [title("Files"), fileTabs("Presets"), disk, uploads, host];
}

// The same list for a phone's backup, which the dashboard never changes.
export function showPhoneFiles(parts) {
  const path = parts.join("/");
  const baseHash = "#/phones/files";
  const host = h("div", {});
  api(`/api/files/phones?path=${encodeURIComponent(path)}`).then((data) => {
    const rows = data.entries.map((entry) => fileRow("phones", baseHash, path, entry, false, () => {}));
    host.replaceChildren(
      crumbs(baseHash, "Phone files", path) ?? "",
      rows.length ? h("ul", { class: "group" }, rows) : empty("Nothing here yet", "Files appear after the phone syncs."));
  }).catch((error) => host.replaceChildren(empty("Can't open this folder", error.message)));
  return [
    title("Phone files", h("a", { class: "icon-btn", href: "#/phones", "aria-label": "Back to Phones" }, icon("back"))),
    host,
  ];
}
