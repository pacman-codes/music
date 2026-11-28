"use strict";
// Route visibility is independent of feature gates (section.hidden).
// No private application state is persisted outside Telegram.
(() => {
  const routes = new Set(["home","search","library","albums","queue","more","artists","history","smart"]);
  const tg = window.Telegram?.WebApp;
  if (!tg?.initData) return;
  const main = document.getElementById("main");
  const nav = document.getElementById("bottom-nav");
  const sections = [...main.querySelectorAll("[data-screen]")];
  const buttons = [...document.querySelectorAll("button[data-route]")];
  let current = "home";
  function navigate(route, options = {}) {
    if (!routes.has(route)) return false;
    const changed = current !== route;
    current = route;
    for (const section of sections) {
      section.classList.toggle("screen-inactive", section.dataset.screen !== route);
    }
    for (const button of buttons) {
      const active = button.dataset.route === route;
      button.classList.toggle("route-active", active);
      if (button.closest("nav")) {
        if (active) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
      }
    }
    main.dataset.activeScreen = route;
    if (changed && options.scroll !== false) window.scrollTo({top:0, behavior:"instant"});
    if (route === "home") tg.BackButton?.hide();
    else tg.BackButton?.show();
    return true;
  }
  window.navigateMusicScreen = navigate;
  for (const button of buttons) {
    button.addEventListener("click", () => navigate(button.dataset.route));
  }
  tg.BackButton?.onClick(() => navigate("home"));
  nav.hidden = false;
  navigate("home", {scroll:false});
})();
