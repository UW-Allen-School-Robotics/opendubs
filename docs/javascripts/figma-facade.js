// Figma facade: a static poster image that swaps to the live, pan/zoom Figma
// embed when the reader clicks it. Keeps pages light + offline-friendly by
// default (just an <img>), and loads the heavy Figma iframe only on demand.
//
// Usage in a page (raw HTML, md_in_html is enabled):
//   <div class="figma-facade" data-embed="<figma embed url>" data-title="...">
//     <img src="assets/diagrams/foo.png" alt="..." loading="lazy">
//     <span class="figma-facade__play">▶ Pan &amp; zoom the live board</span>
//   </div>
(function () {
  function init() {
    document.querySelectorAll(".figma-facade").forEach(function (el) {
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
        iframe.setAttribute("title", el.getAttribute("data-title") || "Figma board");
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
