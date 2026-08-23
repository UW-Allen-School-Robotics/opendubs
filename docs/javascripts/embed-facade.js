// Embed facade: a static poster image that swaps to a live, interactive embed
// (Figma board, Autodesk Fusion viewer, or any iframe-able URL) when the reader
// clicks it. Keeps pages light + offline-friendly by default (just an <img>), and
// loads the heavy iframe only on demand.
//
// Usage in a page (raw HTML; md_in_html is enabled):
//   <div class="embed-facade" data-embed="<iframe url>" data-title="..."
//        style="--embed-aspect: 62%">
//     <img src="assets/.../poster.png" alt="..." loading="lazy">
//     <span class="embed-facade__play">▶ Explore the live model</span>
//   </div>
(function () {
  function init() {
    document.querySelectorAll(".embed-facade").forEach(function (el) {
      if (el.dataset.bound) return;
      el.dataset.bound = "1";
      var url = el.getAttribute("data-embed");
      if (!url) return;
      el.setAttribute("role", "button");
      el.setAttribute("tabindex", "0");
      function activate() {
        if (el.classList.contains("is-active")) return;
        var iframe = document.createElement("iframe");
        iframe.src = url;
        iframe.setAttribute("allowfullscreen", "");
        iframe.setAttribute("title", el.getAttribute("data-title") || "Embedded viewer");
        el.innerHTML = "";
        el.classList.add("is-active");
        el.appendChild(iframe);
      }
      el.addEventListener("click", activate);
      el.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); }
      });
    });
  }
  if (document.readyState !== "loading") init();
  else document.addEventListener("DOMContentLoaded", init);
  // Material for MkDocs uses instant (SPA-style) navigation; re-init on page change.
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(init);
  }
})();
