// The page's entry: the live face (main.js), or the offline render (render_page.js) for `?render=1`.
const render = new URLSearchParams(location.search).get("render") === "1";
// If the page's code fails to load (the dashboard restarting as the page reloaded, 2026-10-19), a face with
// no code would sit there for good: reload in a few seconds instead.
try {
  await import(render ? "./render_page.js" : "./main.js");
} catch (e) {
  if (!render) setTimeout(() => location.reload(), 3000);
  throw e;
}
