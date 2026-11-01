// Addons: optional apps that run beside the dashboard, each at its own address.

import {
  api, closeSheet, confirmSheet, empty, h, icon, openSheet, poll, postJson, title, toast,
} from "./core.js";

const STATES = {
  planned: "Not ready to install yet",
  available: "Not installed",
  installing: "Installing…",
  removing: "Removing…",
  running: "Running",
  stopped: "Stopped",
  problem: "Not responding",
  failed: "Needs attention",
};

let host = null;

async function send(path, data) {
  try {
    await postJson(path, data);
    await render();
  } catch (error) {
    toast(error.message);
  }
}

const installAddon = (addon) => send(`/api/addons/${addon.id}/install`, {});
const control = (addon, action) => send(`/api/addons/${addon.id}/action`, { action });

async function confirmInstall(addon) {
  const go = await confirmSheet({
    heading: `Install ${addon.name}?`,
    message: `${addon.download ? `${addon.download}. ` : ""}The Pi needs internet, and this can take a few minutes.`,
    action: "Install",
  });
  if (go) installAddon(addon);
}

async function confirmRemove(addon) {
  const go = await confirmSheet({
    heading: `Remove ${addon.name}?`,
    message: "The app is removed, but its files stay on the Pi. Installing it again brings them back.",
    action: "Remove",
    danger: true,
  });
  if (go) send(`/api/addons/${addon.id}/remove`, {});
}

async function showPassword(addon) {
  try {
    const { user, password } = await api(`/api/addons/${addon.id}/credentials`);
    openSheet(`${addon.name} sign-in`,
      h("p", { class: "sheet-text" }, `Username: ${user}`),
      h("code", { class: "mono-box" }, password));
  } catch (error) {
    closeSheet();
    toast(error.message);
  }
}

function moreSheet(addon) {
  const item = (label, onclick) => h("button", {
    class: "btn wide",
    type: "button",
    onclick: () => { closeSheet(); onclick(); },
  }, label);
  openSheet(addon.name,
    addon.credentials ? h("button", { class: "btn wide", type: "button", onclick: () => showPassword(addon) }, "Show admin password") : null,
    item("Restart", () => control(addon, "restart")),
    item("Update", () => installAddon(addon)),
    addon.homepage ? h("a", { class: "btn wide", href: addon.homepage, target: "_blank", rel: "noopener" }, "Project page") : null,
    h("button", { class: "btn wide danger-text", type: "button", onclick: () => confirmRemove(addon) }, "Remove"));
}

const isBusy = (addon) => addon.state === "installing" || addon.state === "removing";

function actions(addon) {
  if (isBusy(addon)) return [];
  if (addon.state === "planned") {
    return addon.homepage
      ? [h("a", { class: "btn", href: addon.homepage, target: "_blank", rel: "noopener" }, "Project page")]
      : [];
  }
  if (!addon.installed) {
    return [h("button", { class: "btn primary", type: "button", onclick: () => confirmInstall(addon) }, "Install")];
  }
  const stopped = addon.state === "stopped" || addon.state === "failed";
  if (addon.state === "problem") {
    return [h("button", { class: "btn primary", type: "button", onclick: () => control(addon, "restart") }, "Restart")];
  }
  return [
    addon.state === "running"
      ? h("a", {
        class: "btn primary",
        href: `http://${location.hostname}:${addon.port}${addon.path}`,
        target: "_blank",
        rel: "noopener",
      }, "Open")
      : null,
    h("button", {
      class: "btn",
      type: "button",
      onclick: () => control(addon, stopped ? "start" : "stop"),
    }, stopped ? "Start" : "Stop"),
  ].filter(Boolean);
}

function addonCard(addon) {
  const busy = isBusy(addon);
  return h("div", { class: "card" },
    h("div", { class: "job-head" },
      h("span", { class: "addon-name" }, icon(addon.icon, "row-icon"), h("strong", {}, addon.name)),
      addon.installed && !busy
        ? h("button", {
          class: "icon-btn",
          type: "button",
          "aria-label": `Options for ${addon.name}`,
          onclick: () => moreSheet(addon),
        }, icon("more"))
        : null),
    h("p", { class: "muted small" }, addon.summary),
    h("p", { class: "muted small" }, h("span", { class: addon.state === "running" ? "dot on" : "dot" }), STATES[addon.state]),
    busy ? h("p", { class: "muted small" }, "This can take a few minutes. You can leave this page.") : null,
    addon.message ? h("pre", { class: "log addon-log", tabindex: 0 }, addon.message) : null,
    h("div", { class: "btn-row" }, actions(addon)));
}

async function render() {
  let data;
  try {
    data = await api("/api/addons");
  } catch (error) {
    host.replaceChildren(empty("Can't load addons", error.message));
    return;
  }
  host.replaceChildren(...(data.addons.length
    ? data.addons.map(addonCard)
    : [empty("No addons yet", "Addons added to the project appear here.")]));
}

export function show() {
  host = h("div", {});
  render();
  poll(render, 5000);
  return [title("Addons"), host];
}
