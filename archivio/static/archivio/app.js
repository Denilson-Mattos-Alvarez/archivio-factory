"use strict";

const menuButton = document.querySelector("[data-menu-toggle]");
const sidebar = document.querySelector("[data-sidebar]");
const scrim = document.querySelector("[data-sidebar-scrim]");
function closeMenu() {
  if (!sidebar || !menuButton || !scrim) return;
  sidebar.classList.remove("is-open");
  scrim.hidden = true;
  menuButton.setAttribute("aria-expanded", "false");
}
if (menuButton && sidebar && scrim) {
  menuButton.addEventListener("click", () => {
    const open = !sidebar.classList.contains("is-open");
    sidebar.classList.toggle("is-open", open);
    scrim.hidden = !open;
    menuButton.setAttribute("aria-expanded", String(open));
  });
  scrim.addEventListener("click", closeMenu);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });
}

// Navigation stays usable as ordinary anchors if JavaScript is unavailable.
const tabButtons = Array.from(document.querySelectorAll("[data-tab]"));
const tabPanels = Array.from(document.querySelectorAll("[data-tab-panel]"));
function showTab(name) {
  if (!tabPanels.some((panel) => panel.id === name)) return;
  tabButtons.forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "true" : "false");
  });
  tabPanels.forEach((panel) => { panel.hidden = panel.id !== name; });
}
if (tabButtons.length) {
  showTab(window.location.hash.slice(1) || tabButtons[0].dataset.tab);
  tabButtons.forEach((button) => button.addEventListener("click", (event) => {
    event.preventDefault();
    showTab(button.dataset.tab);
    history.replaceState(null, "", button.getAttribute("href"));
  }));
  window.addEventListener("hashchange", () => showTab(window.location.hash.slice(1)));
}

const productionGrid = document.querySelector("[data-productions]");
const layoutButtons = Array.from(document.querySelectorAll("[data-layout]"));
function setLayout(layout) {
  if (!productionGrid) return;
  productionGrid.classList.toggle("is-list", layout === "list");
  layoutButtons.forEach((button) => {
    const active = button.dataset.layout === layout;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}
if (productionGrid) {
  let preferred = "grid";
  try { preferred = localStorage.getItem("archivio-factory-layout") || "grid"; } catch { /* UI preferences are optional. */ }
  setLayout(preferred);
  layoutButtons.forEach((button) => button.addEventListener("click", () => {
    setLayout(button.dataset.layout);
    try { localStorage.setItem("archivio-factory-layout", button.dataset.layout); } catch { /* Keep the current layout without storing it. */ }
  }));
}

document.querySelectorAll("[data-submit-on-change]").forEach((select) => {
  select.addEventListener("change", () => select.form.requestSubmit());
});

document.querySelectorAll("[data-file-picker]").forEach((input) => {
  input.closest(".file-picker").classList.add("is-enhanced");
  input.addEventListener("change", () => {
    const summary = input.closest(".file-picker").querySelector("[data-file-summary]");
    const names = Array.from(input.files || [], (file) => file.name);
    summary.textContent = names.length ? names.join(", ") : "Nessun file selezionato";
  });
});
