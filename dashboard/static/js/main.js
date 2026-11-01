// Sign in, the tab bar and routing between screens.

import * as downloads from "./downloads.js";
import * as files from "./files.js";
import * as home from "./home.js";
import * as phones from "./phones.js";
import * as tech from "./tech.js";
import { $, api, closeSheet, empty, h, hooks, icon, loadStatus, postJson, stopPolling, title } from "./core.js";

const rtw = {
  show() {
    return [title("RTW"), empty("Coming later", "This section is reserved.")];
  },
};

const screens = { home, files, downloads, phones, rtw, tech };
const tabs = { home: ["Home", "home"], files: ["Files", "folder"], phones: ["Phones", "phone"], rtw: ["RTW", "rtw"], tech: ["Tech", "tech"] };

for (const [name, [label, symbol]] of Object.entries(tabs)) {
  $(`.tabbar [data-tab="${name}"]`).append(icon(symbol, "tab-icon"), h("span", {}, label));
}

function route() {
  if ($("#shell").hidden) return;
  stopPolling();
  closeSheet();
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const name = screens[parts[0]] ? parts[0] : "home";
  const tab = name === "downloads" ? "files" : name;
  document.querySelectorAll(".tabbar a").forEach((link) => {
    const active = link.dataset.tab === tab;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  document.body.dataset.screen = name;
  $("#view").replaceChildren(...screens[name].show(parts.slice(1)));
  window.scrollTo(0, 0);
}

function showLogin() {
  stopPolling();
  closeSheet();
  $("#shell").hidden = true;
  $("#login").hidden = false;
  $("#password").focus();
}

function showShell() {
  $("#login").hidden = true;
  $("#shell").hidden = false;
  route();
}

hooks.unauthorized = showLogin;
hooks.signOut = async () => {
  await postJson("/api/logout", {});
  showLogin();
};

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = $("#login-error");
  error.hidden = true;
  try {
    await postJson("/api/login", { password: $("#password").value });
    $("#password").value = "";
    showShell();
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
  }
});

// A tap on the dimmed area outside a sheet closes it.
$("#sheet").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeSheet();
});

window.addEventListener("hashchange", route);
setInterval(() => { if (!$("#shell").hidden) loadStatus(); }, 20000);

const { authenticated } = await api("/api/session");
if (authenticated) showShell();
else showLogin();
