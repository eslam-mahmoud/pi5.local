// Downloads: fetch video or audio with yt-dlp, saved in folders on the Pi.

import { crumbs, fileRow, fileTabs, showDisk } from "./files.js";
import {
  api, empty, formatBytes, formatRate, h, icon, meter, poll, postJson, title, toast,
} from "./core.js";

const QUALITIES = {
  video: [["best", "Best available"], ["1080", "1080p"], ["720", "720p"], ["480", "480p"]],
  audio: [["mp3", "MP3"], ["m4a", "M4A"]],
};

let jobsHost = null;
let listHost = null;
let footer = null;
let currentPath = "";
let finished = new Set();

function option([value, label], selected) {
  return h("option", { value, selected: value === selected }, label);
}

function field(label, control) {
  return h("label", { class: "field" }, h("span", {}, label), control);
}

function toggle(label, control) {
  return h("label", { class: "switch" }, h("span", {}, label), control);
}

function buildForm() {
  let kind = "video";
  const url = h("input", {
    type: "url", required: true, inputmode: "url", autocomplete: "off",
    placeholder: "Paste a video link", "aria-label": "Video link",
  });
  const quality = h("select", { "aria-label": "Quality" });
  const showQualities = () => quality.replaceChildren(...QUALITIES[kind].map((item) => option(item)));
  showQualities();

  const subtitlesRow = toggle("Subtitles (English, Arabic)", h("input", { type: "checkbox" }));
  const subtitles = subtitlesRow.querySelector("input");
  const playlist = h("input", { type: "checkbox" });
  const folder = h("input", { type: "text", maxlength: 60, placeholder: "Optional", "aria-label": "Folder name" });
  const rate = h("select", {}, [["", "No limit"], ["200K", "200 KB/s"], ["500K", "500 KB/s"], ["1M", "1 MB/s"], ["5M", "5 MB/s"]].map((item) => option(item)));
  const fragments = h("select", {}, [["1", "1"], ["2", "2"], ["4", "4"], ["8", "8"]].map((item) => option(item)));
  const retries = h("input", { type: "number", min: 0, max: 50, value: 10, inputmode: "numeric" });
  const clip = h("input", { type: "text", placeholder: "1:00-2:30", inputmode: "text", autocomplete: "off" });

  const kinds = [["video", "Video"], ["audio", "Audio only"]].map(([value, label]) => {
    const button = h("button", {
      class: value === kind ? "active" : "",
      type: "button",
      onclick: () => {
        kind = value;
        kinds.forEach((other) => other.classList.toggle("active", other === button));
        subtitlesRow.hidden = kind === "audio";
        showQualities();
      },
    }, label);
    return button;
  });

  return h("form", {
    class: "card form",
    onsubmit: async (event) => {
      event.preventDefault();
      try {
        await postJson("/api/downloads", {
          url: url.value,
          kind,
          quality: quality.value,
          folder: folder.value,
          playlist: playlist.checked,
          subtitles: subtitles.checked,
          rate: rate.value,
          fragments: fragments.value,
          retries: retries.value,
          clip: clip.value,
        });
        url.value = "";
        renderJobs();
      } catch (error) {
        toast(error.message);
      }
    },
  },
  url,
  h("div", { class: "segmented" }, kinds),
  field("Quality", quality),
  toggle("Whole playlist", playlist),
  subtitlesRow,
  field("Save in folder (blank uses the channel name)", folder),
  h("details", { class: "more" },
    h("summary", {}, "Download options"),
    h("div", { class: "form" },
      field("Speed limit", rate),
      h("div", { class: "field-row" }, field("Parallel fragments", fragments), field("Retries", retries)),
      field("Only this part of the video", clip))),
  h("button", { class: "btn primary wide", type: "submit" }, "Download"));
}

function folderOf(job) {
  const first = job.files[0];
  return first ? first.split("/").slice(0, -1).join("/") : "";
}

function jobStatus(job) {
  if (job.state === "queued") return "Waiting";
  if (job.state === "cancelled") return job.message || "Cancelled";
  if (job.state === "failed") return job.message;
  if (job.state === "done") {
    const count = job.files.length;
    return `Saved in ${folderOf(job) || "Downloads"}${count > 1 ? ` (${count} files)` : ""}`;
  }
  if (job.stage && job.stage !== "Downloading") return `${job.stage}…`;
  const pieces = [`${Math.round(job.percent)}%`];
  if (job.size) pieces.push(formatBytes(job.size));
  if (job.speed) pieces.push(formatRate(job.speed));
  if (job.eta != null) pieces.push(`${Math.ceil(job.eta / 60)} min left`);
  return job.stage ? pieces.join(" · ") : "Starting…";
}

function jobCard(job) {
  const active = job.state === "queued" || job.state === "running";
  const folder = job.state === "done" ? folderOf(job) : "";
  return h("div", { class: "card" },
    h("div", { class: "job-head" },
      h("strong", {}, job.title),
      h("button", {
        class: "icon-btn",
        type: "button",
        "aria-label": active ? "Cancel download" : "Clear from list",
        onclick: async () => {
          try {
            await api(`/api/downloads/${job.id}`, { method: "DELETE" });
            renderJobs();
          } catch (error) {
            toast(error.message);
          }
        },
      }, icon("close"))),
    h("p", { class: job.state === "failed" ? "error small wrap" : "muted small wrap" }, jobStatus(job)),
    job.state === "running" ? meter(job.percent) : null,
    folder ? h("div", { class: "btn-row" }, h("a", { class: "btn", href: `#/downloads/${folder.split("/").map(encodeURIComponent).join("/")}` }, "Open folder")) : null);
}

async function renderList() {
  if (!listHost) return;
  const baseHash = "#/downloads";
  let data;
  try {
    data = await api(`/api/files/downloads?path=${encodeURIComponent(currentPath)}`);
  } catch (error) {
    listHost.replaceChildren(empty("Can't open this folder", error.message));
    return;
  }
  const rows = data.entries.map((entry) => fileRow("downloads", baseHash, currentPath, entry, true, renderList, data.entries));
  listHost.replaceChildren(...[
    crumbs(baseHash, "Downloads", currentPath),
    rows.length ? h("ul", { class: "group" }, rows) : empty(currentPath ? "This folder is empty" : "Nothing saved yet", currentPath ? "" : "Videos and audio appear here, sorted into folders."),
  ].filter(Boolean));
}

function updateButton(version) {
  const button = h("button", {
    class: "btn",
    type: "button",
    disabled: !version,
    onclick: async () => {
      button.disabled = true;
      button.textContent = "Updating…";
      try {
        const result = await postJson("/api/downloads/update", {});
        toast(result.message || "yt-dlp is up to date");
      } catch (error) {
        toast(error.message);
      }
      renderJobs(true);
    },
  }, "Update");
  return button;
}

async function renderJobs(redrawFooter = false) {
  let data;
  try {
    data = await api("/api/downloads");
  } catch {
    return;
  }
  if (!jobsHost?.isConnected) return;
  jobsHost.replaceChildren(...data.jobs.map(jobCard));
  const done = data.jobs.filter((job) => job.state === "done").map((job) => job.id);
  if (done.some((id) => !finished.has(id))) {
    renderList();
    showDisk(document.querySelector("#view .disk"));
  }
  finished = new Set(done);
  if (redrawFooter || !footer.hasChildNodes()) {
    footer.replaceChildren(
      h("span", {}, data.version ? `yt-dlp ${data.version}` : "yt-dlp isn't installed"),
      updateButton(data.version));
  }
}

export function show(parts) {
  currentPath = parts.join("/");
  listHost = h("div", {});
  renderList();
  if (currentPath) return [title("Files"), fileTabs("Downloads"), listHost];

  const disk = h("div", { class: "disk" });
  jobsHost = h("div", {});
  footer = h("div", { class: "footnote" });
  showDisk(disk);
  renderJobs(true);
  poll(renderJobs, 2500);
  return [title("Files"), fileTabs("Downloads"), disk, buildForm(), jobsHost, listHost, footer];
}
