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
  // a checkbox: `checked` starts from the attribute, `indeterminate` is a
  // property only — markup cannot carry it, which is why the page sets it
  // on the node
  get checked() {
    if (this._checked === undefined) this._checked = "checked" in this.attrs;
    return this._checked;
  }
  set checked(v) { this._checked = !!v; }
  get indeterminate() { return !!this._indeterminate; }
  set indeterminate(v) { this._indeterminate = !!v; }
  get title() { return this.attrs.title || ""; }
  set title(v) { this.setAttr("title", v); }
  insertAdjacentHTML(_pos, html) { this.innerHTML = this._html + html; }
  descendants() { return this.children.flatMap(c => [c, ...c.descendants()]); }
  querySelectorAll(sel) { return this.descendants().filter(c => matchSel(c, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  scrollIntoView() {} focus() {} blur() {}
  addEventListener(k, fn) { this.handlers[k] = fn; }
  click(ev) {
    if (this.attrs.type === "checkbox" && !(ev && ev.keepChecked))
      this.checked = !this.checked;      // the browser flips it before the handler
    const fn = this.onclick || this.handlers.click;
    if (fn) {
      const event = Object.assign({ target: this, preventDefault() {},
                                    stopPropagation() {} }, ev || {});
      try { fn(event); } catch (e) { errors.push(e); }
    }
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
        (node's own fetch schedules on setTimeout).
   The gate of a base beyond the default fills in BEHIND the first paint —
   that is the shipped behaviour, so poll for it the way the browser does
   rather than pretending the first response is final. ---- */
let snapshot;
try {
  for (let i = 0; i < 60; i++) {
    snapshot = await (await fetch(`${ROOT}/api/desk`)).json();
    const bases = new Set(snapshot.queue.rows.map(r => r.base).filter(Boolean));
    const known = Object.keys(snapshot.queue.gates || {});
    if (known.length >= bases.size) break;      // every base's gate has landed
    await new Promise(r => setTimeout(r, 150));
  }
} catch (e) {
  console.log(`\ncannot reach a desk on ${ROOT} (${e.message}) — start one first:\n` +
    `  python3 prdesk.py --provider fixture --repo desk-tests/ui --port ${PORT}\n`);
  process.exit(2);
}

globalThis.setInterval = () => 0;      // the page's own polling stays off
globalThis.fetch = async () => { throw new Error("network is off in this test"); };

/* ---- run the page's script ---- */
const script = html.match(/<script>\n([\s\S]*)\n<\/script>/)[1];
const page = new Function(`${script}\nreturn {applyDesk,applyState,render,renderDetail,select,moveSelection,setSort,visiblePrs,visibleIssues,
  rowClick,togglePick,clearPicks,doRun,MAX_BATCH,
  get state(){return {prs,issues,selected,tab,view,loaded,DESK,truncated,pendingMerge,sort,
                      picked:[...picked],askBatch};},
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
page.watcher = false;
page.render();
ok("standalone Analyze stays enabled without a chat watcher",
   !document.getElementById("aAnalyze").disabled);
page.watcher = true;
page.render();
const actions = document.getElementById("detailActions");
ok("the action button is in the detail head, not a dialog",
   actions.innerHTML.includes("aAnalyze"));
ok("no modal dialog is left in the page", !html.includes("<dialog"));
ok("analyze button carries a real title", /title="[^"]{40,}"/.test(actions.innerHTML));
ok("mutating fetches carry the desk session token",
   html.includes('"X-Git-Workflow-Token":writeToken'));

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

/* ---- 7b. the work the desk now computes, and says it computed ---- */
page.applyDesk(snapshot);
page.render();
ok("the grid provenance is stated, never left ambiguous",
   document.getElementById("noteBox").innerHTML.includes("calcolata dal desk"));
ok("the payload carries the computed grid", !!snapshot.queue.grid);
ok("the grid has the five blocks of pr-triage §5",
   (snapshot.queue.grid.blocks || []).length === 5);
ok("every PR lands in exactly one block", (() => {
  const placed = snapshot.queue.grid.blocks.flatMap(b => b.rows.map(r => r.n));
  return placed.length === snapshot.queue.rows.length &&
         new Set(placed).size === placed.length;
})());

const blocksTab = document.getElementById("tabs").querySelectorAll("button")
  .find(b => b.dataset.v === "blocks");
ok("a Blocks tab shows what the desk computed", !!blocksTab);
blocksTab.click();
ok("the blocks render as their own cards",
   document.getElementById("chaseWrap").innerHTML.includes("Da mergiare subito"));
ok("a block row is clickable through to the detail panel",
   document.getElementById("chaseWrap").querySelectorAll("[data-n]").length > 0);

page.view = "todo";
page.render();
const quadro = document.getElementById("detailGrid");
ok("the detail panel shows the gate of the row's base",
   quadro.innerHTML.includes("Gate di"), quadro.innerHTML.slice(0, 120));
ok("the gate says who may land",
   /riservato a|non protetta|codeowner/.test(quadro.innerHTML),
   quadro.innerHTML.slice(quadro.innerHTML.indexOf("Gate di"),
                          quadro.innerHTML.indexOf("Gate di") + 160));
/* Analizza is THE action button — it hands the PR to the chat. Spiega is a
   fallback that only shows up when the desk cannot answer "what is this for"
   from the data itself. */
ok("Analizza is always offered: it is the action button",
   document.getElementById("detailActions").innerHTML.includes("aAnalyze"));
ok("the detail says what the PR is for, straight from the data",
   /Cosa risolve/.test(quadro.innerHTML) &&
   !!page.state.prs.find(r => r.summary));
ok("Spiega is hidden when the author's own description answers it", (() => {
  const withSummary = page.visiblePrs().find(r => r.summary);
  page.select(withSummary.n);
  return !document.getElementById("detailActions").innerHTML.includes("aExplain");
})());
ok("Spiega appears when there is no description to read", (() => {
  const row = page.state.prs.find(r => r.summary);
  const keep = row.summary;
  row.summary = null;
  page.select(row.n);
  const shown = document.getElementById("detailActions").innerHTML.includes("aExplain");
  row.summary = keep;
  return shown;
})());
ok("a closed issue is named with its title, not just its number",
   page.state.prs.some(r => (r.closes || []).some(c => c.title)));

/* ---- 7d. one press, one hand-over ---- */
const target = page.visiblePrs()[0];
page.select(target.n);
target.requests = { analyze: { status: "queued", at: "10:00:00", kind: "analyze" } };
page.render();
const acts = document.getElementById("detailActions").innerHTML;
ok("an outstanding request locks its button instead of re-arming it",
   acts.includes("in chat") && !acts.includes('id="aAnalyze"'));
ok("the panel says where the ball is",
   /passata alla chat/.test(document.getElementById("detailGrid").innerHTML));
target.requests = { analyze: { status: "done", at: "10:00:00",
                               closed_at: "10:02:00", report: "niente da rispondere" } };
page.render();
ok("a closed request shows its outcome",
   /niente da rispondere/.test(document.getElementById("detailGrid").innerHTML));
ok("and the button comes back", document.getElementById("detailActions")
   .innerHTML.includes('id="aAnalyze"'));
target.requests = { analyze: { status: "failed", at: "10:00:00",
                               report: "gate non passato" } };
page.render();
ok("a failure reads as a failure",
   /gate non passato/.test(document.getElementById("detailGrid").innerHTML));
target.requests = {};

ok("chase blocks carry the dates the message needs",
   Object.values(snapshot.queue.chase).some(t => /\(\d{4}-\d{2}-\d{2}\)/.test(t)));

/* ---- 7c. no invented Italian for a term whose home is English ---- */
const BANNED = ["Situa", "situa", "Solleciti", "mergiabili", "assegnatari"];
const uiText = html.slice(html.indexOf("<body"));
for (const word of BANNED)
  ok(`the UI does not say "${word}"`, !uiText.includes(word),
     uiText.slice(Math.max(0, uiText.indexOf(word) - 40), uiText.indexOf(word) + 40));

/* ---- 7e. the row under the needle ---- */
const runner = page.visiblePrs()[1];
page.applyState({ working: { n: runner.n, msg: "riallineo il branch", at: "19:10:00" },
                  watcher: { alive: true, chat: true }, feed: [] });
page.render();
const tbodyNow = document.getElementById("tbody");
ok("the row the chat is on is marked in the table",
   tbodyNow.querySelectorAll("tr").some(
     tr => +tr.dataset.n === runner.n && tr.classList.contains("working")));
ok("only that row is marked",
   tbodyNow.querySelectorAll("tr").filter(tr => tr.classList.contains("working")).length === 1);
ok("the row carries a live chip, not just a colour",
   tbodyNow.innerHTML.includes("nowChip"));
ok("a bar says which PR and what is happening", (() => {
  const bar = document.getElementById("workingBar");
  return bar.classList.contains("on") && bar.innerHTML.includes(String(runner.n)) &&
         /riallineo il branch/.test(bar.innerHTML);
})());
ok("the bar offers a jump to the row",
   document.getElementById("workingBar").innerHTML.includes("goWorking"));
page.select(runner.n);
ok("the detail panel says the chat is on this one",
   /la chat sta lavorando questa/.test(document.getElementById("detailGrid").innerHTML));
/* ---- 7f. a batch marks every row it is working ---- */
const three = page.visiblePrs().slice(0, 3).map(r => r.n);
page.applyState({ working: { n: three[0], ns: three, items: {}, msg: "3 in parallelo",
                             at: "19:20:00" },
                  watcher: { alive: true, chat: true }, feed: [] });
page.render();
ok("every row of a batch glows, not just the first",
   document.getElementById("tbody").querySelectorAll("tr")
     .filter(tr => tr.classList.contains("working")).length === 3);
ok("the bar names the whole batch", (() => {
  const bar = document.getElementById("workingBar").innerHTML;
  return three.every(n => bar.includes(String(n))) && /in parallelo/.test(bar);
})());
page.applyState({ working: { n: three[0], ns: three,
                             items: { [three[1]]: "giro i test" },
                             msg: "3 in parallelo", at: "19:20:00" },
                  watcher: { alive: true, chat: true }, feed: [] });
page.select(three[1]);
ok("the detail of one batch member shows its own line, not the batch label",
   /giro i test/.test(document.getElementById("detailGrid").innerHTML));

/* ---- 7g. rows picked by hand, with a checkbox ----
   The gesture has to be visible and it must not be the same one that opens
   the row: ticking picks, and the action stays one click further on. */
page.clearPicks();
page.render();
const boxes = () => document.getElementById("tbody").querySelectorAll('input[type=checkbox]');
ok("every row carries a checkbox", boxes().length === page.visiblePrs().length);
ok("the header carries a select-all", !!document.getElementById("pickAll"));

const box = n => boxes().find(b => +b.dataset.pick === n);
box(three[0]).click();
box(three[1]).click();
ok("ticking a box picks the row",
   page.state.picked.length === 2 && page.state.picked.includes(three[0]));
ok("a picked row is marked in the table",
   document.getElementById("tbody").querySelectorAll("tr")
     .filter(tr => tr.classList.contains("picked")).length === 2);
ok("ticking does not move the cursor, so the panel stays put", (() => {
  const before = page.state.selected;
  box(page.visiblePrs()[5].n).click();
  const same = page.state.selected === before;
  box(page.visiblePrs()[5].n).click();       // untick it again
  return same;
})());
ok("unticking removes just that one", (() => {
  box(three[1]).click();
  const only = page.state.picked.length === 1 && page.state.picked.includes(three[0]);
  box(three[1]).click();
  return only;
})());
ok("a plain click on the row never drops the picks", (() => {
  page.rowClick(page.visiblePrs()[4].n, {});
  return page.state.picked.length === 2;
})());
ok("a click that lands on the checkbox does not also open the row", (() => {
  const before = page.state.selected;
  const target = box(page.visiblePrs()[6].n);
  page.rowClick(page.visiblePrs()[6].n, { target });
  const untouched = page.state.selected === before;
  return untouched;
})());
ok("shift-click on a box takes the stretch", (() => {
  page.clearPicks(); page.render();
  const list = page.visiblePrs();
  boxes().find(b => +b.dataset.pick === list[1].n).click();
  const far = boxes().find(b => +b.dataset.pick === list[4].n);
  far.click({ shiftKey: true });
  const got = page.state.picked.slice().sort();
  const want = [list[1].n, list[2].n, list[3].n, list[4].n].sort();
  return JSON.stringify(got) === JSON.stringify(want);
})());
ok("select-all takes every row of THIS view, not the whole queue", (() => {
  page.clearPicks(); page.render();
  document.getElementById("pickAll").click();
  return page.state.picked.length === page.visiblePrs().length &&
         page.state.picked.length < page.state.prs.length;
})());
ok("select-all again clears them", (() => {
  document.getElementById("pickAll").click();
  return page.state.picked.length === 0;
})());
ok("a non-table view leaves no stale checkbox behind", (() => {
  const tabs = document.getElementById("tabs").querySelectorAll("button");
  tabs.find(b => b.dataset.v === "blocks").click();
  const stale = document.getElementById("tbody").querySelectorAll("input[type=checkbox]").length;
  tabs.find(b => b.dataset.v === "todo").click();
  return stale === 0;
})());
ok("the header box shows the in-between state when only some are picked", (() => {
  page.clearPicks(); page.render();
  boxes().find(b => +b.dataset.pick === three[0]).click();
  return document.getElementById("pickAll").indeterminate === true;
})());
page.clearPicks(); page.render();
box(three[0]).click();
box(three[1]).click();
ok("the pick bar says which rows and offers to run them", (() => {
  const bar = document.getElementById("pickBar");
  return bar.classList.contains("on") && bar.innerHTML.includes("pRun");
})());
ok("▶ on several picked rows asks batch or one at a time, it does not guess",
   (() => { page.doRun();
            const bar = document.getElementById("pickBar").innerHTML;
            return page.state.askBatch && /Batch da 2/.test(bar) &&
                   /Una alla volta/.test(bar); })());
ok("the batch it offers never exceeds one answer box", page.MAX_BATCH === 4);
ok("a single picked row is not asked about", (() => {
  page.clearPicks(); page.render();
  boxes().find(b => +b.dataset.pick === three[0]).click();
  page.doRun();
  return !page.state.askBatch;
})());
page.clearPicks();
ok("svuota leaves nothing picked and nothing marked",
   page.state.picked.length === 0 &&
   !document.getElementById("pickBar").classList.contains("on"));

page.applyState({ working: null, watcher: { alive: true, chat: true }, feed: [] });
page.render();
ok("when the loop ends nothing is left glowing",
   !document.getElementById("workingBar").classList.contains("on") &&
   !document.getElementById("tbody").innerHTML.includes("nowChip"));

/* ---- 7h. Chase is people, not PRs ---- */
const tabsNow = () => document.getElementById("tabs").querySelectorAll("button");
tabsNow().find(b => b.dataset.v === "chase").click();
ok("Chase shows one card per person",
   document.getElementById("chaseWrap").querySelectorAll("[data-copy]").length ===
     Object.keys(snapshot.queue.chase).length);
ok("each card carries the message to paste, whole",
   document.getElementById("chaseWrap").querySelectorAll("[data-copy]")
     .every(b => (b.attrs["data-copy"] || "").includes("#")));
ok("the login is not upper-cased: it is a case-sensitive handle",
   /\.chaseCard h2\{[^}]*text-transform:none/.test(html));
ok("a card says how many and since when",
   document.getElementById("chaseWrap").innerHTML.includes("chaseCount"));
ok("no PR detail panel under the chase blocks — the unit here is a person",
   document.getElementById("detail").innerHTML === "" &&
   document.getElementById("detail").style.display === "none");
ok("no PR tabs leak into this view",
   !document.getElementById("detail").innerHTML.includes("detailTabs"));
tabsNow().find(b => b.dataset.v === "todo").click();
ok("leaving Chase brings the detail panel back",
   document.getElementById("detail").innerHTML.includes("detailGrid") &&
   document.getElementById("detail").style.display !== "none");

/* ---- 8. the issue desk uses the same panel ---- */
page.applyDesk({ ...snapshot, meta: { ...snapshot.meta, desk: "issue" } });
page.render();
ok("issue desk renders its own detail panel",
   document.getElementById("detail").innerHTML.includes("issue #"));
ok("issue desk has its own tabs",
   document.getElementById("detail").innerHTML.includes('data-t="analisi"'));
ok("issue desk shows the cross-check the desk computed",
   document.getElementById("detailGrid").innerHTML.includes("Cross-check"));
ok("the issue shortlist is computed, and labelled as computed",
   !!snapshot.issues.shortlist && snapshot.issues.shortlist_computed !== false);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
