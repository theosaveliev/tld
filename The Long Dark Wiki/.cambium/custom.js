const openButton = document.getElementById("menu-open-button");
const closeButton = document.getElementById("menu-close-button");
const menuPanel = document.getElementById("menu-panel");
const screenShade = document.getElementById("screen-shade");
const inertElements = document.querySelectorAll("[data-inert-in-menu]");

function setMenuOpen(isOpen) {
  menuPanel?.classList.toggle("menu-active", isOpen);
  screenShade?.classList.toggle("screen-shade-active", isOpen);
  openButton?.setAttribute("aria-expanded", String(isOpen));

  inertElements.forEach((element) => {
    if (isOpen) {
      element.setAttribute("inert", "");
    } else {
      element.removeAttribute("inert");
    }
  });
}

// Use capture-phase listeners to replace Maple's toggle-based handlers with
// explicit state changes. This remains correct if the bundled handler loads too.
openButton?.setAttribute("aria-expanded", "false");
openButton?.addEventListener("click", (event) => {
  event.stopImmediatePropagation();
  setMenuOpen(true);
}, true);

closeButton?.addEventListener("click", (event) => {
  event.stopImmediatePropagation();
  setMenuOpen(false);
}, true);

screenShade?.addEventListener("click", (event) => {
  event.stopImmediatePropagation();
  setMenuOpen(false);
}, true);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.stopImmediatePropagation();
    setMenuOpen(false);
  }
}, true);
