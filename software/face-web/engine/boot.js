// The page's entry: the live face (main.js), or the offline render (render_page.js) for `?render=1`.
const render = new URLSearchParams(location.search).get("render") === "1";
await import(render ? "./render_page.js" : "./main.js");
