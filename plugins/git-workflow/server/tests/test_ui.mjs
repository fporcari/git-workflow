/* UI test — loads the real static/index.html against a live fixture desk.
 *
 *   node tests/test_ui.mjs [port]          # port of a running fixture desk
 *   GIT_WORKFLOW_STATE_DIR=$(mktemp -d) \
 *     python3 prdesk.py --provider fixture --repo genropy/genropy --port 8397
 *
 * The state dir is not optional. A published triage is durable by design, so a
 * desk started on the real one answers the next run's first /api/desk with the
 * previous run's grid, and the checks on a virgin fetch fail for no reason of
 * their own. `run.sh` does this for you.
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
  setAttribute(k, v) { this.setAttr(k, String(v)); }
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
const docHandlers = {};
globalThis.document = {
  getElementById: id => registry.get(id) || null,
  body,
  querySelector: sel => allEls().find(e => matchSel(e, sel)) || null,
  querySelectorAll: sel => allEls().filter(e => matchSel(e, sel)),
  createElement: t => new El(t),
  // recorded, not swallowed: the page's own visibility handler is under test
  addEventListener: (k, fn) => { docHandlers[k] = fn; },
  visibilityState: "visible",
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
    if (snapshot.meta?.provider !== "fixture") {
      throw new Error("UI tests require the fixture provider; refusing to write to this desk");
    }
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

/* ---- the explicit triage, run for real: the desk computes and publishes the
   grid itself, so the page only ever has to paint what /api/desk hands it ---- */
let triaged;
try {
  await fetch(`${ROOT}/api/rows`, {method: "POST",
    headers: {"X-Git-Workflow-Token": snapshot.meta.write_token}});
  triaged = await (await fetch(`${ROOT}/api/desk`)).json();
} catch (e) {
  console.log(`\ncannot publish a triage on ${ROOT} (${e.message})\n`);
  process.exit(2);
}

globalThis.setInterval = () => 0;      // the page's own polling stays off
globalThis.fetch = async () => { throw new Error("network is off in this test"); };

/* ---- run the page's script ---- */
const script = html.match(/<script>\n([\s\S]*)\n<\/script>/)[1];
const page = new Function(`${script}\nreturn {applyDesk,applyState,render,renderSync,loadDesk,select,toggleOpen,moveSelection,setSort,visiblePrs,visibleIssues,rk,setMode,setView,openPalette,closePalette,commands,toggleScope,renderThreads,
  rowClick,togglePick,clearPicks,doRun,pending,startPending,endPending,sendReview,sendClose,bulkAction,firstView,stanceOf,viewCount,toggleDrawer,pollHeaders,
  get state(){return {prs,issues,selected,openRow,view,loaded,DESK,truncated,pendingMerge,sort,prepare,
                      picked:[...picked]};},
  set view(v){view=v;}, set query(v){query=v;}, set agent(v){agentReady=v;}};`)();

/* a row's name on the page is repo#n: the numbers the checks pick by are
   turned into it here, once */
const K = n => page.rk(page.state.prs.find(r => r.n === n) ||
                       page.state.issues.find(r => r.n === n));

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
const rowTrs = () => tbody.querySelectorAll("tr").filter(tr => tr.classList.contains("row"));
ok("table has one row per visible PR",
   rowTrs().length === page.visiblePrs().length,
   `${rowTrs().length} vs ${page.visiblePrs().length}`);
ok("every row is clickable", rowTrs().every(tr => "n" in tr.dataset));
ok("the selected row is marked", tbody.innerHTML.includes("selected"));
ok("rows are striped, so the eye keeps the line",
   rowTrs().some(tr => tr.classList.contains("alt")) && /tr\.row\.alt td\{background:var\(--zebra\)\}/.test(html));

/* ---- 4. one header row, the toolbar its own colour, the log at the bottom ---- */
ok("no row of metrics above the table: the header is one row",
   !html.includes('class="summary"') && !html.includes("metric"));
ok("the sections are a segmented control, not plain buttons",
   /\.modeTabs button\[aria-selected=true\]\{background:var\(--raised\)/.test(html));
ok("the toolbar has a colour of its own",
   /\.queueTools\{[^}]*background:var\(--bar\)/.test(html) && /\.topbar\{[^}]*background:var\(--head\)/.test(html));
ok("the log and the jobs live in a drawer at the bottom, closed at first",
   "hidden" in document.getElementById("drawer").attrs && document.getElementById("drawer").hidden !== false &&
   html.indexOf('id="drawer"') > html.indexOf('id="tableWrap"'));
page.toggleDrawer(true);
ok("the log opens on demand", document.getElementById("drawer").hidden === false);
page.toggleDrawer(false);
ok("the page follows the system theme, and has a dark one",
   html.includes('@media (prefers-color-scheme: dark)') && html.includes(':root[data-theme="dark"]') &&
   !!document.getElementById("btnTheme"));
ok("the close-desk button is in view, with its words", (() => {
  const b = document.getElementById("btnStop");
  return b && /Chiudi il desk/.test(b.textContent) && b.classList.contains("stop");
})());

/* ---- 5. a row opens in place: no panel below, no Analizza ---- */
ok("no detail panel and no splitter in the page",
   !html.includes('id="detail"') && !html.includes('id="splitter"'));
ok("Analizza is gone: the preparation reads the PRs", !html.includes("aAnalyze") && !html.includes("▶ Analizza"));
ok("no modal dialog is left in the page", !html.includes("<dialog"));
ok("mutating fetches carry the desk session token",
   html.includes('"X-Git-Workflow-Token":writeToken'));
const firstRow = page.visiblePrs()[0];
page.toggleOpen(firstRow);
const openTr = () => tbody.querySelectorAll("tr").find(tr => tr.classList.contains("expand"));
/* the shim keeps markup on the node it was set on: an open row reads it from the table's */
const openHtml = () => {
  const h = tbody.innerHTML, at = h.indexOf('<tr class="expand"');
  return at < 0 ? "" : h.slice(at, h.indexOf("</tr>", at));
};
ok("a click opens the row under itself", !!openTr() && openTr().dataset.xk === page.rk(firstRow));
ok("the open row says what the PR is for", /Cosa risolve/.test(openHtml()));
ok("the open row shows the gate of its base", /gate di/.test(openHtml()));
ok("the open row links to the provider", /target="_blank"/.test(openHtml()));
page.toggleOpen(firstRow);
ok("a second click folds it", !openTr());
ok("every row has its own link out, without opening it",
   (tbody.innerHTML.match(/class="btn ghBtn"/g) || []).length === rowTrs().length);

/* ---- 6. the stances: approvable, to reject, doubtful ---- */
const stances = snapshot.stances;
const stepNs = id => ((stances.review.steps.find(s => s.id === id) || {}).rows || []).map(c => c.n);
ok("the snapshot carries the stances", !!stances && !!stances.review && !!stances.prepare);
ok("each stance has its filter, with its count",
   ["approve", "changes", "doubt"].every(id => page.viewCount(id) === stepNs(id).length));
ok("the desk opens on the first filter with work in it",
   page.firstView() === (["approve", "changes", "doubt"].find(id => stepNs(id).length) || "todo"));
ok("a review asked of you is never twice: not in Da fare when it has a stance", (() => {
  page.setView("todo");
  const todo = new Set(page.visiblePrs().map(r => r.n));
  return ["approve", "changes", "doubt"].every(id => stepNs(id).every(n => !todo.has(n)));
})());
const approveN = stepNs("approve")[0], changesN = stepNs("changes")[0], doubtN = stepNs("doubt")[0];
ok("the fixture lands a row in each stance", approveN && changesN && doubtN);
page.setView("approve");
ok("an approvable row offers Approva without opening",
   tbody.innerHTML.includes(`data-quick="approve" data-k="${K(approveN)}"`));
page.setView("doubt");
page.toggleOpen(page.state.prs.find(r => r.n === doubtN));
ok("an open doubt says the doubt, and offers the three keys",
   /Il dubbio/.test(openHtml()) && /data-act="approve"/.test(openHtml()) &&
   /data-act="changes"/.test(openHtml()) && /data-act="skip"/.test(openHtml()));
page.setView("changes");
const changesRow = page.state.prs.find(r => r.n === changesN);
page.toggleOpen(changesRow);
const area = () => openTr().querySelectorAll("textarea")[0];
ok("a row to reject shows its motivation, editable", !!area() && area().dataset.draft === K(changesN));
{
  const sent = [];
  const offline = globalThis.fetch;
  globalThis.fetch = async (path, opts) => {
    sent.push({path, body: JSON.parse((opts && opts.body) || "{}")});
    return {status: 202, headers: {get: () => null}, json: async () => ({via: "chat"})};
  };
  try {
    area().value = "Per favore aggiungi un test.";
    area().oninput();
    openTr().querySelectorAll("button").find(b => b.dataset.act === "changes").click();
    await new Promise(r => setTimeout(r, 0));
    const review = sent.find(p => p.path === "/api/review");
    ok("Chiedi modifiche sends the motivation as edited, on the head shown",
       review && review.body.event === "changes" && review.body.items[0].n === changesN &&
       review.body.items[0].body === "Per favore aggiungi un test." &&
       review.body.items[0].head === (stances.review.steps.find(s => s.id === "changes").rows[0].head ?? changesRow.head));
    sent.length = 0;
    page.setView("approve");
    page.clearPicks();
    const ap = page.visiblePrs().slice(0, 2);
    ap.forEach(r => page.togglePick(r));
    const bulk = page.bulkAction();
    ok("picked approvable rows are approved together",
       bulk && /Approva/.test(bulk.label) && document.getElementById("pickBar").innerHTML.includes("pBulk"));
    document.getElementById("pBulk").click();
    await new Promise(r => setTimeout(r, 0));
    const both = sent.find(p => p.path === "/api/review");
    ok("one click, one review per picked row",
       both && both.body.event === "approve" && both.body.items.length === ap.length);
    page.clearPicks();
  } finally {
    globalThis.fetch = offline;
  }
}
{
  const unread = JSON.parse(JSON.stringify(snapshot));
  const moved = unread.stances.review.steps.find(s => s.id === "approve").rows.splice(0);
  const review = unread.stances.review.steps.find(s => s.id === "review");
  review.rows.push(...moved.map((c, i) => i ? c : {...c, chip: "legge…"}));
  page.applyDesk(unread);
  page.setView("review");
  ok("an empty analysis stance drops its filter, Da rivedere stays",
     !document.getElementById("tabs").innerHTML.includes("Approvabili") &&
     document.getElementById("tabs").innerHTML.includes("Da rivedere") &&
     !document.getElementById("tabs").innerHTML.includes("In analisi"));
  ok("a review nobody has read yet offers ▶ pr-loop on its row",
     (tbody.innerHTML.match(/data-quick="loop"/g) || []).length === page.visiblePrs().length &&
     page.visiblePrs().length === review.rows.length);
  ok("a row an analysis job is reading says what it is doing",
     tbody.innerHTML.includes("legge…"));
  const sent = [];
  const offline = globalThis.fetch;
  globalThis.fetch = async (path, opts) => {
    sent.push({path, body: JSON.parse(opts.body || "{}")});
    return {status: 202, headers: {get: () => null}, json: async () => ({runs: [{via: "chat"}]})};
  };
  try {
    tbody.querySelector('[data-quick="loop"]').click();
    await new Promise(r => setTimeout(r, 0));
    const run = sent.find(p => p.path === "/api/run");
    ok("▶ pr-loop on a row runs the loop on that PR alone",
       run && run.body.flow === "pr-loop" && run.body.batch === 1 &&
       run.body.ns.length === 1 && run.body.ns[0] === page.visiblePrs()[0].n);
  } finally {
    globalThis.fetch = offline;
    page.clearPicks();
  }
  page.applyDesk(snapshot);
}

/* ---- 7. the preparation: small in the status bar while it runs, a banner when it fails ---- */
page.applyState({ prepare: { pr: { status: "running", phrase: "triage PR · in corso", due: [], landed: [], failed: {} },
                             issue: { status: "running", phrase: "triage issue · in corso", due: [], landed: [], failed: {} } } });
page.render();
ok("a running triage shows in the status bar, every kind",
   document.getElementById("prepChip").hidden === false &&
   /<footer class="statusBar"[^]*id="prepChip"[^]*<\/footer>/.test(html) &&
   /triage PR · in corso/.test(document.getElementById("prepChip").innerHTML) &&
   /triage issue · in corso/.test(document.getElementById("prepChip").innerHTML));
page.applyState({ prepare: { pr: { status: "running", phrase: "x", due: [1, 2, 3], landed: [1], failed: {} },
                             issue: { status: "done", phrase: "", due: [], landed: [], failed: {} } } });
page.render();
ok("a night run that analyzes says how far it is",
   /analisi PR · 1 di 3/.test(document.getElementById("prepChip").innerHTML) &&
   !/issue/.test(document.getElementById("prepChip").innerHTML));
page.applyState({ prepare: { pr: { status: "failed", phrase: "x", report: "interrotto: gh api failed",
                                   at: "2026-10-07T15:16:15", due: [], landed: [], failed: {} },
                             issue: { status: "done", phrase: "", due: [], landed: [], failed: {} } } });
page.render();
const prepBox = document.getElementById("prepBox");
ok("a failed preparation says why, instead of nothing to do",
   prepBox.classList.contains("on") && /non riuscita/.test(prepBox.innerHTML) &&
   /gh api failed/.test(prepBox.innerHTML) && /15:16/.test(prepBox.innerHTML));
{
  const sent = [];
  const offline = globalThis.fetch;
  globalThis.fetch = async (path, opts) => {
    sent.push({path, body: JSON.parse((opts && opts.body) || "{}")});
    return {status: 202, headers: {get: () => null}, json: async () => ({started: ["pr"]})};
  };
  try {
    document.getElementById("btnPrepare").click();
    await new Promise(r => setTimeout(r, 0));
    ok("Riprova starts the preparation again",
       sent.some(p => p.path === "/api/prepare" && p.body.kinds[0] === "pr"));
  } finally {
    globalThis.fetch = offline;
  }
}
page.applyState({ prepare: snapshot.stances.prepare });
page.render();
ok("a done preparation leaves no banner and no chip",
   !document.getElementById("prepBox").classList.contains("on") && document.getElementById("prepChip").hidden);

/* ---- 8. selection, sorting, filtering ---- */
page.setView("all");
const first = page.rk(page.visiblePrs()[0]);
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

/* ---- 9. honesty notes ---- */
page.applyDesk({ ...snapshot, queue: { ...snapshot.queue, truncated: true, total: 999 } });
page.render();
ok("a truncated queue is reported, never hidden",
   document.getElementById("noteBox").innerHTML.includes("999"));
page.applyDesk({ ...snapshot, issues: { ...snapshot.issues, truncated: true, total: 228 } });
page.render();
ok("a truncated issue list is reported too",
   document.getElementById("noteBox").innerHTML.includes("228"));

/* ---- 10. explicit triage ---- */
page.applyDesk(triaged);
page.render();
ok("explicit pr-triage makes every matching row current",
   page.state.prs.every(r => r.triage_status === "current"));
ok("the server, not the page, reconciled them",
   triaged.queue.triage_complete && !html.includes("applyPrTriage"));
ok("the triage button shows that nothing is pending",
   document.getElementById("btnTriage").textContent.includes("✓"));
ok("no Blocks filter: the stances say it better",
   !document.getElementById("tabs").querySelectorAll("button").some(b => b.dataset.v === "blocks"));

/* ---- 11. one press, one hand-over ---- */
page.setView("all");
const target = page.state.prs.find(r => r.summary && !page.stanceOf(r));
const keep = target.summary;
target.summary = null;
page.select(target);
page.toggleOpen(target);
ok("Spiega appears when there is no description to read", /id="aExplain"/.test(openHtml()));
target.requests = { explain: { status: "queued", at: "10:00:00", kind: "explain" } };
page.render();
ok("an outstanding request locks its button instead of re-arming it",
   openHtml().includes("richiesta precedente") && !openHtml().includes('id="aExplain"'));
ok("the open row says where the ball is", /richiesta alle/.test(openHtml()));
target.requests = { explain: { status: "done", at: "10:00:00", closed_at: "10:02:00", report: "niente da dire" } };
page.render();
ok("a closed request shows its outcome", /niente da dire/.test(openHtml()));
target.requests = { explain: { status: "failed", at: "10:00:00", report: "gate non passato" } };
page.render();
ok("a failure reads as a failure", /gate non passato/.test(openHtml()));
target.requests = {};
target.summary = keep;
page.toggleOpen(target);
ok("chase blocks carry the dates the message needs",
   Object.values(triaged.queue.chase).every(t => /\(\d{4}-\d{2}-\d{2}\)/.test(t)));

/* ---- 12. no invented Italian for a term whose home is English ---- */
const BANNED = ["Situa", "situa", "Solleciti", "mergiabili", "assegnatari", "Filoni"];
const uiText = html.slice(html.indexOf("<body"));
for (const word of BANNED)
  ok(`the UI does not say "${word}"`, !uiText.includes(word),
     uiText.slice(Math.max(0, uiText.indexOf(word) - 40), uiText.indexOf(word) + 40));

/* ---- 13. the jobs, in the drawer and on the status line ---- */
const runner = page.visiblePrs()[1];
const liveJob = { id: "job-live", kind: "operation", status: "running", agent: "codex",
  request: { flow: "pr-loop", ns: [runner.n], batch: 1 },
  progress: { stage: "testing", detail: "Command · pytest tests/test_api.py", elapsed: 68 },
  events: [
    { at: "19:09:58", stage: "inspecting", detail: "Command · gh pr view" },
    { at: "19:10:00", stage: "testing", detail: "Command · pytest tests/test_api.py" },
  ] };
page.applyState({ agent: { mode: "on-demand", busy: true, jobs: [liveJob] }, feed: [] });
page.render();
const jobPanel = () => document.getElementById("jobPanel");
ok("a job shows its live phase and elapsed time",
   /verifica/.test(jobPanel().innerHTML) && /1:08/.test(jobPanel().innerHTML));
ok("observable agent activity is shown below the current phase",
   /gh pr view/.test(jobPanel().innerHTML) && /pytest tests\/test_api.py/.test(jobPanel().innerHTML));
ok("the status line names the job at work",
   /pr-loop/.test(document.getElementById("statusMsg").innerHTML) && /1:08/.test(document.getElementById("statusMsg").innerHTML));
page.startPending("flow:pr-triage", "pr-triage");
ok("a pressed button paints a card before the server has answered",
   /pr-triage/.test(jobPanel().innerHTML) && /in coda/.test(jobPanel().innerHTML) &&
   /pr-triage/.test(document.getElementById("statusMsg").innerHTML));
ok("the live job keeps its own card while another click waits",
   /pytest tests\/test_api.py/.test(jobPanel().innerHTML));
page.endPending("flow:pr-triage");
ok("the waiting card goes when the server answers", !/in coda/.test(jobPanel().innerHTML));
const REPORT = "pr-loop su genropy/genropy, working set [1183, 1099].\n\n"
  + "AZIONI AUTOMATICHE: nessuna.\n\nPROPOSTE: #1183 review --request-changes.";
const reportState = extra => ({ agent: { mode: "on-demand", busy: false }, feed: [],
  runs: { "pr-loop": { status: "needs-input", report: REPORT, at: "14:24:15" } }, ...extra });
page.applyState(reportState({ agent: { mode: "on-demand", busy: true, jobs: [liveJob] } }));
page.render();
ok("live progress is drawn above the report a past run left behind",
   jobPanel().innerHTML.indexOf("pytest tests/test_api.py") < jobPanel().innerHTML.indexOf("PROPOSTE: #1183"));
page.applyState(reportState());
page.render();
ok("a finished run leaves its report, whole, with its name and time",
   jobPanel().innerHTML.includes("PROPOSTE: #1183 review --request-changes") &&
   /14:24:15/.test(jobPanel().innerHTML) && /serve una decisione/.test(jobPanel().innerHTML));
jobPanel().querySelector("[data-run-toggle]").click();
ok("the report folds away without losing the card",
   !jobPanel().innerHTML.includes("PROPOSTE: #1183") && jobPanel().innerHTML.includes("pr-loop"));
jobPanel().querySelector("[data-run-toggle]").click();
jobPanel().querySelector("[data-run-close]").click();
ok("dismissing it empties the panel", jobPanel().innerHTML === "");
page.applyState(reportState());
page.render();
ok("a dismissed report does not come back on the next poll", jobPanel().innerHTML === "");
page.applyState({ agent: { mode: "on-demand", busy: false }, feed: [],
                  runs: { "pr-loop": { status: "done", report: "report di ieri", at: "2026-08-31T23:59:59" },
                          "issue-loop": { status: "done", report: "report di oggi", at: "2026-09-01T00:00:01" } } });
page.render();
ok("the newest report is selected across midnight, shown as time only",
   jobPanel().innerHTML.includes("report di oggi") && !jobPanel().innerHTML.includes("report di ieri") &&
   jobPanel().innerHTML.includes("00:00:01") && !jobPanel().innerHTML.includes("2026-09-01"));
page.applyState({ agent: { mode: "on-demand", busy: false }, runs: {},
                  feed: [{ at: "15:02:00", msg: "pr-triage: 3 verdetti" }] });
page.render();
ok("with nothing at work the status line says the last thing that happened",
   /pr-triage: 3 verdetti/.test(document.getElementById("statusMsg").innerHTML));

/* ---- 14. attached chat ---- */
page.applyState({ chat: { attached: false } });
ok("no chat attached: the chip stays hidden", document.getElementById("chatState").hidden === true);
page.applyState({ chat: { attached: true, at: "15:02:11" } });
ok("an attached chat shows the chip", document.getElementById("chatState").hidden === false);
page.applyState({ chat: { attached: false } });
for (const [status, locks] of [["taken", true], ["preparing", true], ["needs-input", false], ["stale", false]])
  ok(`a ${status} chat request ${locks ? "locks" : "frees"} its button`,
     !!page.pending({ requests: { order: { status, at: "15:03:00", via: "chat" } } }, "order") === locks);

/* ---- 15. the row under the needle ---- */
page.applyState({ working: { n: runner.n, msg: "riallineo il branch", at: "19:10:00" },
                  agent: { mode: "on-demand", busy: true }, feed: [] });
page.render();
ok("the row a loop is on is marked in the table",
   rowTrs().some(tr => +tr.dataset.n === runner.n && tr.classList.contains("working")));
ok("only that row is marked", rowTrs().filter(tr => tr.classList.contains("working")).length === 1);
ok("the row carries a live chip, not just a colour", tbody.innerHTML.includes("nowChip"));
ok("the status line says which PR and what is happening",
   document.getElementById("statusMsg").innerHTML.includes(String(runner.n)) &&
   /riallineo il branch/.test(document.getElementById("statusMsg").innerHTML));
const three = page.visiblePrs().slice(0, 3).map(r => r.n);
page.applyState({ working: { n: three[0], ns: three, items: { [three[1]]: "giro i test" },
                             msg: "3 in parallelo", at: "19:20:00" },
                  agent: { mode: "on-demand", busy: true }, feed: [] });
page.render();
ok("every row of a batch glows, not just the first",
   rowTrs().filter(tr => tr.classList.contains("working")).length === 3);
page.toggleOpen(page.state.prs.find(r => r.n === three[1]));
ok("an open batch member shows its own line, not the batch label", /giro i test/.test(openHtml()));
page.toggleOpen(page.state.prs.find(r => r.n === three[1]));

/* ---- 16. rows picked by hand, with a checkbox ---- */
page.clearPicks();
page.render();
const boxes = () => tbody.querySelectorAll('input[type=checkbox]');
ok("every row carries a checkbox", boxes().length === page.visiblePrs().length);
ok("the header carries a select-all", !!document.getElementById("pickAll"));
const box = n => boxes().find(b => b.dataset.pick === K(n));
box(three[0]).click();
box(three[1]).click();
ok("ticking a box picks the row",
   page.state.picked.length === 2 && page.state.picked.includes(K(three[0])));
ok("a picked row is marked in the table", rowTrs().filter(tr => tr.classList.contains("picked")).length === 2);
ok("ticking does not move the cursor", (() => {
  const before = page.state.selected;
  box(page.visiblePrs()[5].n).click();
  const same = page.state.selected === before;
  box(page.visiblePrs()[5].n).click();
  return same;
})());
ok("a plain click on the row never drops the picks", (() => {
  page.rowClick(page.rk(page.visiblePrs()[4]), {});
  page.rowClick(page.rk(page.visiblePrs()[4]), {});
  return page.state.picked.length === 2;
})());
ok("a click that lands on the checkbox does not also open the row", (() => {
  const before = page.state.openRow;
  page.rowClick(page.rk(page.visiblePrs()[6]), { target: box(page.visiblePrs()[6].n) });
  return page.state.openRow === before;
})());
ok("shift-click on a box takes the stretch", (() => {
  page.clearPicks(); page.render();
  const list = page.visiblePrs();
  boxes().find(b => b.dataset.pick === page.rk(list[1])).click();
  boxes().find(b => b.dataset.pick === page.rk(list[4])).click({ shiftKey: true });
  const got = page.state.picked.slice().sort();
  const want = [list[1], list[2], list[3], list[4]].map(page.rk).sort();
  return JSON.stringify(got) === JSON.stringify(want);
})());
ok("select-all takes every row of THIS view, then clears them", (() => {
  page.clearPicks(); page.setView("todo");
  document.getElementById("pickAll").click();
  const all = page.state.picked.length === page.visiblePrs().length && page.state.picked.length < page.state.prs.length;
  document.getElementById("pickAll").click();
  page.setView("all");
  return all && page.state.picked.length === 0;
})());
ok("a non-table view leaves no stale checkbox behind", (() => {
  page.setView("chase");
  const stale = tbody.querySelectorAll("input[type=checkbox]").length;
  page.setView("all");
  return stale === 0;
})());
page.clearPicks(); page.render();
box(three[0]).click();
ok("the header box shows the in-between state when only some are picked",
   document.getElementById("pickAll").indeterminate === true);
box(three[1]).click();
ok("the pick bar says which rows and offers to run them",
   document.getElementById("pickBar").classList.contains("on") &&
   document.getElementById("pickBar").innerHTML.includes("pRun"));
{
  const sent = [];
  const offline = globalThis.fetch;
  globalThis.fetch = async (path, opts) => {
    sent.push({path, body: JSON.parse(opts.body || "{}")});
    return {status: 202, headers: {get: () => null}, json: async () => ({runs: [{via: "chat"}]})};
  };
  try {
    page.doRun();
    await new Promise(r => setTimeout(r, 0));
    const run = sent.find(p => p.path === "/api/run");
    ok("▶ on several picked rows runs them as one batch, without asking",
       run && run.body.batch === 2 && run.body.ns.length === 2);
    sent.length = 0;
    page.clearPicks(); page.render();
    boxes().find(b => b.dataset.pick === K(three[0])).click();
    page.doRun();
    await new Promise(r => setTimeout(r, 0));
    const one = sent.find(p => p.path === "/api/run");
    ok("a single picked row runs alone", one && one.body.batch === 1);
  } finally {
    globalThis.fetch = offline;
  }
}
page.clearPicks();
ok("svuota leaves nothing picked and nothing marked",
   page.state.picked.length === 0 && !document.getElementById("pickBar").classList.contains("on"));
page.applyState({ working: null, agent: { mode: "on-demand", busy: false }, feed: [] });
page.render();
ok("when the loop ends nothing is left glowing", !tbody.innerHTML.includes("nowChip"));

/* ---- 17. a completed provider mutation refreshes facts, not triage ---- */
let forcedReads = 0;
globalThis.fetch = async url => {
  if (url === "/api/fetch") forcedReads++;
  return {status: 304, headers: {get: () => null}, json: async () => ({})};
};
page.applyState({provider_refresh: {token: "ui-refresh-1"}, working: null,
                 agent: {mode: "on-demand", busy: false}, feed: []});
await Promise.resolve();
ok("a provider mutation forces one factual refresh", forcedReads === 1);
ok("a stale-while-revalidate response gets a short repaint retry",
   html.includes('t.source==="stale"') && html.includes('},7000)'));
globalThis.fetch = async () => { throw new Error("network is off in this test"); };

/* ---- 18. Chase is people, not PRs ---- */
page.setView("chase");
ok("Chase shows one card per person",
   document.getElementById("chaseWrap").querySelectorAll("[data-copy]").length ===
     Object.keys(triaged.queue.chase).length);
ok("each card carries the message to paste, whole",
   document.getElementById("chaseWrap").querySelectorAll("[data-copy]")
     .every(b => (b.attrs["data-copy"] || "").includes("#")));
ok("the login is not upper-cased: it is a case-sensitive handle",
   /\.chaseCard h2\{[^}]*text-transform:none/.test(html));
ok("a reply owed sits under the person waiting for it, not in a message",
   Object.keys(triaged.queue.replies).length > 0 &&
   Object.entries(triaged.queue.replies).every(([who, items]) =>
     document.getElementById("chaseWrap").innerHTML.includes(`@${who} <span class="chaseCount">aspetta una tua risposta`) &&
     items.every(r => document.getElementById("chaseWrap").innerHTML.includes(`data-n="${r.n}"`))));
ok("the table steps aside for the cards", document.getElementById("tableWrap").style.display === "none");
page.setView("todo");
ok("Da fare leaves out a PR where only a comment is due",
   triaged.queue.rows.some(r => r.state === "reply") &&
   triaged.queue.rows.filter(r => r.state === "reply")
     .every(r => !tbody.innerHTML.includes(`data-n="${r.n}"`)));

/* ---- 19. the issues ---- */
page.applyDesk({ ...snapshot, meta: { ...snapshot.meta, desk: "issue" } });
page.render();
const closeNs = ((snapshot.stances.issue.steps.find(s => s.id === "close") || {}).rows || []).map(c => c.n);
ok("the issue section opens on the issues to close when there are some",
   page.state.DESK === "issue" && (!closeNs.length || page.state.view === "close"));
ok("Da chiudere lists exactly the fixed ones", page.viewCount("close") === closeNs.length);
if (closeNs.length) {
  page.toggleOpen(page.state.issues.find(r => r.n === closeNs[0]));
  ok("an open issue to close shows its comment and the key",
     /Commento di chiusura/.test(openHtml()) && /data-act="close"/.test(openHtml()));
  const sent = [];
  const offline = globalThis.fetch;
  globalThis.fetch = async (path, opts) => {
    sent.push({path, body: JSON.parse((opts && opts.body) || "{}")});
    return {status: 202, headers: {get: () => null}, json: async () => ({via: "chat"})};
  };
  try {
    openTr().querySelectorAll("button").find(b => b.dataset.act === "close").click();
    await new Promise(r => setTimeout(r, 0));
    const close = sent.find(p => p.path === "/api/close");
    ok("Chiudi sends the comment that names the PR",
       close && close.body.items[0].n === closeNs[0] && /Fixed by #/.test(close.body.items[0].body));
  } finally {
    globalThis.fetch = offline;
  }
}
page.setView("all");
page.toggleOpen(page.visibleIssues()[0]);
ok("an open issue shows the cross-check the desk computed", /PR aperte/.test(openHtml()));
ok("the issue shortlist is computed on every read", !!snapshot.issues.shortlist);
ok("every issue row says whether it is in it", snapshot.issues.rows.every(r => "in_shortlist" in r));
ok("the page no longer hunts the shortlist array per row", !html.includes("shortlist.rows.find"));
page.applyState({ agent: { mode: "on-demand", busy: false }, feed: [],
                  runs: { "issue-loop": { status: "needs-input", at: "15:02:11",
                                          report: "issue-loop: 3 lavorate, 1 serve una decisione" } } });
page.render();
ok("the issue section shows a finished issue-loop report too",
   jobPanel().innerHTML.includes("issue-loop: 3 lavorate, 1 serve una decisione") &&
   /15:02:11/.test(jobPanel().innerHTML));

/* ---- 20. a tab left in the background must not keep an old render (#2) ---- */
page.applyDesk(snapshot);
page.render();
ok("the page registers a visibility handler at all", typeof docHandlers.visibilitychange === "function");
let deskReads = 0;
globalThis.fetch = async url => {
  if (String(url).startsWith("/api/desk")) deskReads++;
  return {status: 304, headers: {get: () => null}, json: async () => ({})};
};
document.visibilityState = "hidden";
document.hidden = true;
docHandlers.visibilitychange();
await Promise.resolve();
ok("going away does not spend a read", deskReads === 0);
ok("a poll from a hidden page is not a use of the desk",
   page.pollHeaders("e1")["X-Git-Workflow-Background"] === "1");
document.visibilityState = "visible";
document.hidden = false;
ok("a poll from a page in view is one", !("X-Git-Workflow-Background" in page.pollHeaders(null)));
docHandlers.visibilitychange();
await Promise.resolve();
ok("coming back reconciles at once, without waiting for the next tick", deskReads === 1);
globalThis.fetch = async () => { throw new Error("network is off in this test"); };

const label = () => document.getElementById("syncLabel").textContent;
page.applyDesk(snapshot);
page.renderSync();
ok("the label is the age of the paint, not the clock it was made at", /^⟳ \d+s$/.test(label()), label());
ok("a fresh paint is not marked stale", !document.getElementById("btnFetch").classList.contains("stale"));
const realNow = Date.now;
try {
  Date.now = () => realNow() + 80000;
  page.renderSync();
  ok("a paint older than two polls says so in the label", /^⟳ 1m$/.test(label()), label());
  ok("and marks the button, so a frozen page announces itself",
     document.getElementById("btnFetch").classList.contains("stale"));
} finally {
  Date.now = realNow;
}
ok("the poll interval and the stale threshold are named, not buried",
   /const POLL=30000,STALE_PAINT=70/.test(html));
globalThis.fetch = async () => ({status: 304, headers: {get: () => null}, json: async () => ({})});
Date.now = () => realNow() + 80000;
try {
  page.renderSync();
  ok("a paint nothing has confirmed still goes stale", /^⟳ 1m$/.test(label()), label());
  await page.loadDesk(false);
  ok("a 304 counts as a confirmation, not as a poll that did nothing", /^⟳ 0s$/.test(label()), label());
  ok("and it takes the stale mark off", !document.getElementById("btnFetch").classList.contains("stale"));
} finally {
  Date.now = realNow;
  globalThis.fetch = async () => { throw new Error("network is off in this test"); };
}

/* ---- 21. one page, three sections ---- */
page.applyDesk(triaged);
page.setMode("pr");
page.render();
const modes = document.getElementById("modeTabs").querySelectorAll("button");
ok("the page offers PR, Issue and A chi tocca as one desk",
   modes.map(b => b.dataset.mode).join() === "pr,issue,threads" && /A chi tocca/.test(document.getElementById("modeTabs").innerHTML));
page.setMode("issue");
ok("switching to Issue paints the issue rows without a reload",
   page.state.DESK === "issue" && rowTrs().length === page.visibleIssues().length);
page.setMode("threads");
const threadsBox = document.getElementById("threadsWrap");
ok("A chi tocca pairs the issues with their PRs, grouped by who must move",
   threadsBox.classList.contains("on") && /thrHead/.test(threadsBox.innerHTML) && triaged.threads.groups.length > 0);
const node = threadsBox.querySelectorAll("[data-goto]")[0];
node.click();
ok("a node opens its PR or issue in its own section, open in place",
   page.state.DESK === node.dataset.goto && page.state.selected === node.dataset.k && page.state.openRow === node.dataset.k);
page.setMode("pr");

/* ---- 22. ⌘K ---- */
page.openPalette();
ok("the palette opens with the sections and the desk commands",
   !document.getElementById("palette").hidden &&
   page.commands().some(c => c.group === "Vai a" && c.label === "A chi tocca") &&
   page.commands().some(c => c.label === "Rileggi il provider") &&
   !page.commands().some(c => c.label === "Analizza"));
page.closePalette();
ok("and closes", document.getElementById("palette").hidden);

/* ---- 23. a scope of several repositories ---- */
const [repoA, repoB] = ["acme/acme-engine", "acme/acme-ext"];
const split = (rows, n) => rows.map((r, i) => ({...r, repo: i < n ? repoA : repoB,
                                                label: `${i < n ? "engine" : "ext"} #${r.n}`}));
const scoped = {...triaged,
  meta: {...triaged.meta, repo: "acme", scope: {name: "acme", members: [
    {repo: repoA, label: "engine", clone: true, provider: "forgejo"},
    {repo: repoB, label: "ext", clone: false, provider: "forgejo"}]}},
  queue: {...triaged.queue, rows: split(triaged.queue.rows, 3)},
  issues: {...triaged.issues, rows: split(triaged.issues.rows, 3)}};
page.applyDesk(scoped);
page.setMode("pr", true);
page.setView("all");
ok("every row names its repository when the desk covers several",
   /repoChip">engine</.test(tbody.innerHTML) && /repoChip">ext</.test(tbody.innerHTML));
const extRow = page.state.prs.find(r => r.repo === repoB);
page.toggleOpen(extRow);
ok("a repository without a clone says what stays off", /non ha un clone locale/.test(openHtml()));
page.toggleOpen(extRow);
page.toggleScope(true);
ok("the scope popover lists the members and their clones",
   /acme\/acme-ext/.test(document.getElementById("scopePop").innerHTML) &&
   /nessun clone locale/.test(document.getElementById("scopePop").innerHTML));
page.toggleScope(false);
const posted = [];
globalThis.fetch = async (path, opts) => {
  posted.push({path, body: JSON.parse(opts.body || "{}")});
  return {status: 202, headers: {get: () => null}, json: async () => ({runs: [
    {via: "chat", repo: repoA}, {via: "chat", repo: repoB}]})};
};
try {
  page.clearPicks();
  page.togglePick(page.state.prs.find(r => r.repo === repoA));
  page.togglePick(extRow);
  page.doRun();
  await new Promise(r => setTimeout(r, 0));
  const run = posted.find(p => p.path === "/api/run");
  ok("a run across repositories sends each row with its repository",
     run && run.body.items.length === 2 && run.body.items.some(i => i.repo === repoB && i.n === extRow.n));
} finally {
  globalThis.fetch = async () => { throw new Error("network is off in this test"); };
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
