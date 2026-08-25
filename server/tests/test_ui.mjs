/* UI test — loads the real static/index.html against a live fixture desk.
 *
 *   node tests/test_ui.mjs [port]          # port of a running fixture desk
 *   python3 prdesk.py --provider fixture --repo genropy/genropy --port 8397
 *
 * No browser and no dependencies: a small DOM shim plus fetch against the
 * real server, so the page's own render path is what gets exercised. It
 * catches the failures that actually happen — a render that throws, a
 * missing field, a button wired to nothing, a detail tab that blanks.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const PORT = process.argv[2] || "8397";
const ROOT = `http://127.0.0.1:${PORT}`;

let pass = 0, fail = 0;
const ok = (name, cond, extra) => {
  if (cond) { pass++; console.log(`  ok   ${name}`); }
  else { fail++; console.log(`  FAIL ${name}${extra ? " — " + extra : ""}`); }
};

/* ---- a DOM small enough to read, real enough to run the page ----
 * A tree, not a flat list: the page renders one container's innerHTML and
 * then looks up ids that only exist inside it, so descendants and
 * getElementById have to see the same nodes.
 */
const registry = new Map();
const VOID = new Set(["br", "hr", "img", "input", "meta", "link"]);

class El {
  constructor(tag, attrs) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = []; this.parent = null;
    this.dataset = {}; this.style = {}; this.attrs = {}; this.handlers = {};
    this._html = ""; this._text = ""; this.value = "";
    this.classList = {
      _s: new Set(),
      add: (...c) => c.forEach(x => this.classList._s.add(x)),
      remove: (...c) => c.forEach(x => this.classList._s.delete(x)),
      toggle: (c, on) => on ? this.classList._s.add(c) : this.classList._s.delete(c),
      contains: c => this.classList._s.has(c),
    };
    for (const [k, v] of Object.entries(attrs || {})) this.setAttr(k, v);
  }
  setAttr(k, v) {
    this.attrs[k] = v;
    if (k === "class") String(v).split(/\s+/).filter(Boolean).forEach(c => this.classList.add(c));
    if (k.startsWith("data-"))
      this.dataset[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = v;
  }
  get id() { return this.attrs.id || ""; }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this.children = build(this._html, this); }
  get textContent() {
    return this._text || this.children.map(c => c.textContent).join("");
  }
  set textContent(v) { this._text = String(v); this.children = []; this._html = ""; }
  get disabled() { return !!this.attrs.disabled; }
  set disabled(v) { this.attrs.disabled = v; }
  get title() { return this.attrs.title || ""; }
  set title(v) { this.setAttr("title", v); }
  insertAdjacentHTML(_pos, html) { this.innerHTML = this._html + html; }
  descendants() { return this.children.flatMap(c => [c, ...c.descendants()]); }
  querySelectorAll(sel) { return this.descendants().filter(c => matchSel(c, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  scrollIntoView() {} focus() {} blur() {}
  addEventListener(k, fn) { this.handlers[k] = fn; }
  click() {
    const fn = this.onclick || this.handlers.click;
    if (fn) { try { fn({ target: this, preventDefault() {}, stopPropagation() {} }); }
              catch (e) { errors.push(e); } }
  }
}

/* Build a tree from rendered markup, registering anything with an id. */
function build(html, parent) {
  const out = [];
  const stack = [];
  const token = /<(\/?)(\w+)([^>]*?)(\/?)>|([^<]+)/g;
  let m;
  const push = node => {
    const top = stack[stack.length - 1];
    if (top) { node.parent = top; top.children.push(node); }
    else { node.parent = parent; out.push(node); }
  };
  while ((m = token.exec(html))) {
    if (m[5] !== undefined) {                        // text
      const top = stack[stack.length - 1];
      if (top) top._text += m[5];
      continue;
    }
    const [, closing, tag, rawAttrs, selfClose] = m;
    if (closing) { stack.pop(); continue; }
    const attrs = {};
    let a; const attr = /([\w-]+)(?:\s*=\s*"([^"]*)")?/g;
    while ((a = attr.exec(rawAttrs))) attrs[a[1]] = a[2] === undefined ? "" : a[2];
    const node = new El(tag, attrs);
    push(node);
    if (node.id) registry.set(node.id, node);
    if (!selfClose && !VOID.has(tag.toLowerCase())) stack.push(node);
  }
  return out;
}

function matchSel(el, sel) {
  return sel.split(",").map(s => s.trim()).some(s => {
    const m = s.match(/^(\w+|\*)?(?:\[([\w-]+)(?:=["']?([\w-]+)["']?)?\])?((?:\.[\w-]+)*)$/);
    if (!m) return false;
    if (m[1] && m[1] !== "*" && el.tagName !== m[1].toUpperCase()) return false;
    if (m[2]) {
      if (!(m[2] in el.attrs)) return false;
      if (m[3] !== undefined && String(el.attrs[m[2]]) !== m[3]) return false;
    }
    for (const c of (m[4] || "").split(".").filter(Boolean))
      if (!el.classList.contains(c)) return false;
    return true;
  });
}

/* ---- the page's environment ---- */
const html = readFileSync(join(HERE, "..", "static", "index.html"), "utf8");
const errors = [];
const body = new El("body");
const allEls = () => [body, ...body.descendants()];
globalThis.document = {
  getElementById: id => registry.get(id) || null,
  body,
  querySelector: sel => allEls().find(e => matchSel(e, sel)) || null,
  querySelectorAll: sel => allEls().filter(e => matchSel(e, sel)),
  createElement: t => new El(t),
  addEventListener: () => {},
  title: "",
};
Object.defineProperty(globalThis, "navigator",
  { value: { clipboard: { writeText: async () => {} } }, configurable: true });
globalThis.window = globalThis;

// the page's static markup, so every id in index.html resolves
body.innerHTML = html.slice(html.indexOf("<main"), html.indexOf("<script>"));

/* ---- 0. the real payload, fetched BEFORE the timers are stubbed
        (node's own fetch schedules on setTimeout) ---- */
let snapshot;
try {
  snapshot = await (await fetch(`${ROOT}/api/desk`)).json();
} catch (e) {
  console.log(`\ncannot reach a desk on ${ROOT} (${e.message}) — start one first:\n` +
    `  python3 prdesk.py --provider fixture --repo genropy/genropy --port ${PORT} --no-prefetch\n`);
  process.exit(2);
}

globalThis.setInterval = () => 0;      // the page's own polling stays off
globalThis.fetch = async () => { throw new Error("network is off in this test"); };

/* ---- run the page's script ---- */
const script = html.match(/<script>\n([\s\S]*)\n<\/script>/)[1];
const page = new Function(`${script}\nreturn {applyDesk,render,renderDetail,select,moveSelection,setSort,visiblePrs,visibleIssues,
  get state(){return {prs,issues,selected,tab,view,loaded,DESK,truncated,pendingMerge,sort};},
  set view(v){view=v;}, set query(v){query=v;}, set watcher(v){watcherAlive=v;}};`)();

/* ---- 1. what the server handed over ---- */
ok("server answers /api/desk in one round trip",
   snapshot.meta && snapshot.queue && snapshot.issues && snapshot.state);
ok("queue carries rows", (snapshot.queue.rows || []).length > 0);
ok("issues carry rows", (snapshot.issues.rows || []).length > 0);

/* ---- 2. the page digests it without throwing ---- */
page.applyDesk(snapshot);
page.render();
ok("first render throws nothing", errors.length === 0, errors[0] && errors[0].message);
ok("rows landed in the page", page.state.prs.length === snapshot.queue.rows.length);
ok("a row is selected by default", page.state.selected !== null);

/* ---- 3. the table ---- */
const tbody = document.getElementById("tbody");
ok("table has one tr per visible row",
   tbody.querySelectorAll("tr").length === page.visiblePrs().length,
   `${tbody.querySelectorAll("tr").length} vs ${page.visiblePrs().length}`);
ok("every row is clickable to select",
   tbody.querySelectorAll("tr").every(tr => "n" in tr.dataset));
ok("the selected row is marked",
   tbody.innerHTML.includes("selected"));

/* ---- 4. the detail panel — the prototype's element the desk had lost ---- */
const detail = document.getElementById("detail");
ok("detail panel renders below the table", detail.innerHTML.includes("detailGrid"));
ok("detail head names the PR", /PR #\d+/.test(detail.innerHTML));
ok("detail has the prototype's four tabs",
   ["quadro", "analisi", "thread", "bozza"].every(t => detail.innerHTML.includes(`data-t="${t}"`)));

for (const t of ["quadro", "analisi", "thread", "bozza"]) {
  errors.length = 0;
  const before = errors.length;
  const tabs = document.getElementById("detailTabs");
  const btn = tabs.querySelectorAll("button").find(b => b.dataset.t === t);
  btn.click();
  const grid = document.getElementById("detailGrid");
  ok(`tab ${t} renders content`, grid.innerHTML.length > 80 && errors.length === before,
     errors[0] && errors[0].message);
}

/* ---- 5. actions are wired and act directly (no modal in between) ---- */
page.watcher = true;
page.render();
const actions = document.getElementById("detailActions");
ok("the action button is in the detail head, not a dialog",
   actions.innerHTML.includes("aAnalyze"));
ok("no modal dialog is left in the page", !html.includes("<dialog"));
ok("analyze button carries a real title", /title="[^"]{40,}"/.test(actions.innerHTML));

/* ---- 6. selection, sorting, filtering ---- */
const first = page.visiblePrs()[0].n;
page.moveSelection(1);
ok("arrow keys move the selection", page.state.selected !== first);
page.setSort("n");
const ns = page.visiblePrs().map(r => r.n);
ok("sorting by # actually sorts", ns.every((v, i) => i === 0 || ns[i - 1] >= v) ||
                                  ns.every((v, i) => i === 0 || ns[i - 1] <= v));
page.query = "zzzzzz-nothing-matches";
page.render();
ok("an empty filter result renders the empty state",
   document.getElementById("empty").style.display === "flex");
page.query = "";

/* ---- 7. honesty banners ---- */
page.applyDesk({ ...snapshot, queue: { ...snapshot.queue, truncated: true, total: 999 } });
page.render();
ok("a truncated queue is reported, never hidden",
   document.getElementById("noteBox").innerHTML.includes("999"));

page.applyDesk({ ...snapshot, issues: { ...snapshot.issues, truncated: true, total: 228 } });
page.render();
ok("a truncated issue list is reported too",
   document.getElementById("noteBox").innerHTML.includes("228"));

/* ---- 8. the issue desk uses the same panel ---- */
page.applyDesk({ ...snapshot, meta: { ...snapshot.meta, desk: "issue" } });
page.render();
ok("issue desk renders its own detail panel",
   document.getElementById("detail").innerHTML.includes("issue #"));
ok("issue desk has its own tabs",
   document.getElementById("detail").innerHTML.includes('data-t="analisi"'));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
