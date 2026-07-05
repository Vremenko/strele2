/**
 * Vgradnja grafov strel na spletno stran.
 *
 * Primer:
 *   <div data-strele-chart="daily" data-strele-days="30"></div>
 *   <script src="https://tvoj-strežnik/public/embed.js" data-strele-api="https://tvoj-strežnik"></script>
 */
(function () {
  "use strict";

  const script = document.currentScript;
  const apiBase = (script && (script.getAttribute("data-strele-api") || script.dataset.streleApi)) || "";
  const embedPath = (script && script.getAttribute("data-strele-embed")) || "/public/embed.html";
  const embedBase = apiBase.replace(/\/$/, "") + embedPath;

  const IFRAME_ATTRS = [
    "chart",
    "days",
    "day",
    "period",
    "source",
    "controls",
    "stats",
    "title",
    "refresh",
    "theme",
    "credit",
    "height",
  ];

  function buildSrc(el) {
    const params = new URLSearchParams();
    const chart = el.getAttribute("data-strele-chart") || "daily";
    params.set("chart", chart);

    for (const name of IFRAME_ATTRS) {
      if (name === "chart") continue;
      const value = el.getAttribute(`data-strele-${name}`);
      if (value != null && value !== "") {
        params.set(name, value);
      }
    }

    if (apiBase) {
      params.set("api", apiBase.replace(/\/$/, ""));
    }

    return `${embedBase}?${params.toString()}`;
  }

  function mount(el) {
    if (el.querySelector("iframe[data-strele-embed]")) return;

    const iframe = document.createElement("iframe");
    iframe.dataset.streleEmbed = "1";
    iframe.src = buildSrc(el);
    iframe.title = el.getAttribute("data-strele-title") || "Graf strel v Sloveniji";
    iframe.loading = "lazy";
    iframe.setAttribute("referrerpolicy", "no-referrer-when-downgrade");
    iframe.style.cssText = [
      "display:block",
      "width:100%",
      "border:0",
      "min-height:200px",
      el.getAttribute("data-strele-height") ? `height:${el.getAttribute("data-strele-height")}px` : "",
    ].filter(Boolean).join(";");

    el.appendChild(iframe);
    return iframe;
  }

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.type !== "strele-embed-resize") return;

    const iframes = document.querySelectorAll("iframe[data-strele-embed]");
    for (const iframe of iframes) {
      if (iframe.contentWindow === event.source) {
        iframe.style.height = `${Math.max(120, data.height)}px`;
      }
    }
  });

  function init() {
    document.querySelectorAll("[data-strele-chart]").forEach(mount);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.StreleEmbed = { mount, buildSrc };
})();
