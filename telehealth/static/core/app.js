document.querySelectorAll("[data-menu]").forEach((button) => {
  const sidebar = document.querySelector("#sidebar");
  const scrim = document.querySelector("[data-menu-close]");
  const closeMenu = () => {
    document.body.classList.remove("menu-open");
    button.setAttribute("aria-expanded", "false");
    scrim?.setAttribute("tabindex", "-1");
  };
  button.addEventListener("click", () => {
    const open = document.body.classList.toggle("menu-open");
    button.setAttribute("aria-expanded", String(open));
    scrim?.setAttribute("tabindex", open ? "0" : "-1");
    if (open) sidebar?.querySelector("a")?.focus();
  });
  scrim?.addEventListener("click", () => {
    closeMenu();
    button.focus();
  });
  sidebar?.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeMenu));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("menu-open")) {
      closeMenu();
      button.focus();
    }
    if (event.key === "Tab" && document.body.classList.contains("menu-open") && sidebar) {
      const focusable = [...sidebar.querySelectorAll('a, button:not([disabled]), [tabindex]:not([tabindex="-1"])')];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const panels = document.querySelector("[data-tab-panels]");
if (panels) {
  const active = panels.dataset.activeTab || "overview";
  panels.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.panel !== active;
  });
  document.querySelectorAll("[data-tab]").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === active);
    if (tab.dataset.tab === active) tab.setAttribute("aria-current", "page");
  });
}

document.querySelector("[data-error-summary]")?.focus();

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", () => {
    const button = form.querySelector('button[type="submit"], button:not([type])');
    if (!button || button.disabled) return;
    button.dataset.originalLabel = button.textContent;
    button.textContent = button.dataset.submitLabel || "Working...";
    button.disabled = true;
    form.setAttribute("aria-busy", "true");
  });
});

window.addEventListener("pageshow", () => {
  document.querySelectorAll('form[aria-busy="true"]').forEach((form) => {
    form.removeAttribute("aria-busy");
    const button = form.querySelector("button[disabled]");
    if (button) {
      button.disabled = false;
      button.textContent = button.dataset.originalLabel || button.textContent;
    }
  });
});
