/* UI test — loads the real static/index.html against a live fixture desk
 * that the morning's preparation has already filled.
 *
 *   node tests/test_ui.mjs [port]          # port of a running fixture desk
 *
 * `run.sh` builds that desk: a throwaway state dir, tests/seed_ui.py running
 * the real preparation with the fake agent for `--me genro`, then the desk
 * with --no-prepare. A state dir is not optional: the analyses are durable,
 * and a desk on the real one would paint yesterday's.
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
  set innerHTML(v) { this._html = String(v); this._text = ""; this.children = build(this._html, this); }
  get textContent() {
    return this._text + this.children.map(c => c.textContent).join("");
  }
  set textContent(v) { this._text = String(v); this.children = []; this._html = ""; }
  get disabled() { return "disabled" in this.attrs && this.attrs.disabled !== false; }
  set disabled(v) { if (v) this.attrs.disabled = ""; else delete this.attrs.disabled; }
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
      (top || parent)._text += m[5];
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
  documentElement: new El("html"),
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

/* ---- 0. the real payload, fetched BEFORE the network is cut ---- */
let snapshot;
try {
  snapshot = await (await fetch(`${ROOT}/api/desk`)).json();
  if (snapshot.meta?.provider !== "fixture")
    throw new Error("UI tests require the fixture provider; refusing to write to this desk");
} catch (e) {
  console.log(`\ncannot reach a desk on ${ROOT} (${e.message}) — run tests/run.sh\n`);
  process.exit(2);
}

globalThis.setInterval = () => 0;      // the page's own polling stays off
const opened = [];
globalThis.open = (url, name) => opened.push([url, name]);
let posted = [];
const offline = async () => { throw new Error("network is off in this test"); };
globalThis.fetch = offline;
const recording = answer => async (path, opts) => {
  if (!opts || opts.method !== "POST") throw new Error("reads are off in this test");
  posted.push({path, body: JSON.parse((opts && opts.body) || "{}")});
  return {ok: true, status: 202, headers: {get: () => null},
          json: async () => (answer || {queued: true, via: "chat"})};
};
const settle = () => new Promise(r => setTimeout(r, 0));

/* ---- run the page's script ---- */
const script = html.match(/<script>\n([\s\S]*)\n<\/script>/)[1];
const page = new Function(`${script}\nreturn {applyDesk,applyWizard,applyState,render,setSection,setStep,
  primary,decideDoubt,openZoom,closeZoom,onKey,toggleTheme,currentStep,stepRows,checked,key,
  get state(){return {section,doubtAt,zoomed,drawerOpen,drafts,off:[...off]};},
  set zoomCache(v){zoomCache=v;}, get wiz(){return wiz;}};`)();
await settle();

const W = snapshot.wizard;
const $ = id => document.getElementById(id);
const acts = (name, where) => (where || document).querySelectorAll("[data-act]")
  .filter(e => e.dataset.act === name);
const text = id => $(id).textContent;
const chat = attached => page.applyState({...snapshot.state, chat: {attached, at: "10:00"}});
const rowsOf = (section, step) => (W[section].steps.find(s => s.id === step) || {}).rows || [];

/* ---- 1. the payload the page is drawn from ---- */
ok("the desk serves the wizard in its one round trip",
   W && ["review", "mine", "issue", "whose", "prepare"].every(k => k in W));
ok("the preparation filled the steps before the page opened",
   rowsOf("review", "approve").length && rowsOf("review", "changes").length &&
   rowsOf("review", "doubt").length && W.review.pending.length === 0);
ok("and says when it did", /^preparata/.test(W.prepare.pr.phrase), W.prepare.pr.phrase);

/* ---- 2. the header: one row, four sections ---- */
page.applyDesk(snapshot);
chat(false);
page.render();
ok("first render throws nothing", errors.length === 0, errors[0] && errors[0].message);
const sections = acts("section", $("seg"));
ok("one header row with Da rivedere, Mie, Issue and A chi tocca",
   sections.map(b => b.dataset.id).join() === "review,mine,issue,whose");
ok("each section says how many rows it holds",
   new RegExp(`Da rivedere\\s*${W.review.count}`).test(sections[0].textContent),
   sections[0].textContent);
ok("Filoni is gone, and so are the preview and Analizza",
   !/Filoni|Analizza/.test(html) && !$("detail"));
ok("the chat state is in the header", /chat non collegata/.test(text("chat")));

/* ---- 3. Approvabili: prechecked, one key, why line, git icon ---- */
ok("Da rivedere opens on its first non-empty step", page.currentStep() === "approve");
const approve = rowsOf("review", "approve");
const boxes = () => $("content").querySelectorAll("input").filter(i => i.attrs.type === "checkbox");
ok("one row per approvable PR, every one prechecked",
   boxes().length === approve.length && boxes().every(b => b.checked));
ok("every row carries its why line", $("content").querySelectorAll("span.why").length === approve.length);
ok("and a git icon", acts("gh", $("content")).length === approve.length);
const cta = () => acts("cta")[0];
ok("the key counts the rows: Approva tutte e N",
   cta().textContent.includes(`Approva tutte e ${approve.length}`), cta().textContent);
ok("without an attached chat a review cannot leave", cta().disabled &&
   /serve la chat collegata/.test($("content").textContent));
boxes()[0].click();
ok("taking a check off changes the count",
   cta().textContent.includes(`Approva le ${approve.length - 1} spuntate`), cta().textContent);
chat(true);
page.render();
globalThis.fetch = recording();
await page.primary();
await settle();
let review = posted.find(p => p.path === "/api/review");
ok("Approva posts exactly the checked rows, with their heads",
   review && review.body.event === "approve" &&
   review.body.items.length === approve.length - 1 &&
   review.body.items.every(i => "head" in i && i.n !== approve[0].n),
   JSON.stringify(review && review.body).slice(0, 200));
posted = []; globalThis.fetch = offline;

/* ---- 4. Da respingere: the motivation under the selected row ---- */
page.setStep("changes");
const changes = rowsOf("review", "changes");
const area = () => $("content").querySelectorAll("textarea")[0];
ok("the first row opens with Claude's motivation, editable", area() &&
   area().textContent.includes(changes[0].draft), area() && area().textContent);
area().value = "Please split the two changes.";
area().handlers.input();
ok("the key names the requests", /Invia le \d+ richieste|Invia la richiesta/.test(cta().textContent));
globalThis.fetch = recording();
await page.primary();
await settle();
review = posted.find(p => p.path === "/api/review");
ok("Invia sends a Request changes with the text as edited",
   review && review.body.event === "changes" &&
   review.body.items[0].body === "Please split the two changes." &&
   review.body.items.length === changes.length);
posted = []; globalThis.fetch = offline;

/* ---- 5. Dubbie: one at a time, A R S ---- */
page.setStep("doubt");
const doubts = rowsOf("review", "doubt");
ok("one doubt at a time, with where it stands",
   $("content").textContent.includes(`dubbia 1 di ${doubts.length}`) &&
   $("content").querySelectorAll("i").length >= doubts.length);
ok("the doubt and Claude's leaning are on the page",
   $("content").textContent.includes(doubts[0].doubt) && /Claude propende per/.test($("content").textContent));
page.onKey({key: "j", target: {}});
ok("j moves to the next doubt", page.state.doubtAt === 1 &&
   $("content").textContent.includes(`dubbia 2 di ${doubts.length}`));
globalThis.fetch = recording({skipped: [doubts[1].n]});
page.onKey({key: "s", target: {}});
await settle();
ok("S puts it off to tomorrow", posted.some(p => p.path === "/api/review" &&
   p.body.event === "skip" && p.body.items[0].n === doubts[1].n));
posted = [];
page.onKey({key: "a", target: {}});
await settle();
ok("A approves the doubt in view", posted.some(p => p.body.event === "approve" &&
   p.body.items[0].n === doubts[1].n));
posted = [];
page.onKey({key: "r", target: {}});
await settle();
ok("R sends the leaning's text", posted.some(p => p.body.event === "changes" &&
   p.body.items[0].body === doubts[1].draft));
posted = []; globalThis.fetch = offline;

/* ---- 6. the zoom: the whole situation, Esc back ---- */
const k = page.key(doubts[0]);
page.zoomCache = {[k]: {problem: "cosa fa in breve", verified: ["i test passano"],
  not_verified: ["le pagine dei clienti"], timeline: [{on: "2026-08-25", text: "apre"},
  {on: "oggi", text: "tocca a te", now: true}], state: {tests: "SUCCESS", merge: "BLOCKED",
  conflicts: "nessuno", reviewers: ["cgabriel"]}, closes: [1146],
  hunk: {path: "gnrjs/gnrbag.js", header: "@@ -1 +1 @@ x", lines: ["-a", "+b"]}}};
page.openZoom(k);
ok("space or the link widens on one PR", !$("zoom").hidden && page.state.zoomed === k);
const zoomText = $("zoom").textContent;
ok("in brief, why it is doubtful, the hunk, what was and was not verified",
   ["cosa fa in breve", "Perché è dubbia", "gnrjs/gnrbag.js", "i test passano",
    "Non verificato: le pagine dei clienti"].every(t => zoomText.includes(t)), zoomText.slice(0, 300));
ok("the story, the state, the linked issues",
   ["La storia", "tocca a te", "BLOCKED", "cgabriel", "#1146"].every(t => zoomText.includes(t)));
page.onKey({key: "Escape", target: {}});
ok("Esc goes back to the list", $("zoom").hidden && page.state.zoomed === null);

/* ---- 7. the git icon opens GitHub in one named window ---- */
acts("gh", $("content"))[0].click();
ok("the git icon opens GitHub in the same named window",
   opened.length === 1 && opened[0][1] === "github" && /^https:\/\/github\.com\//.test(opened[0][0]));

/* ---- 8. Fatto and A chi tocca ---- */
page.setStep("done");
ok("Fatto says what the session sent and whose move it is now",
   /Oggi/i.test($("content").textContent) && /A chi tocca adesso/i.test($("content").textContent) &&
   $("content").querySelectorAll("div").filter(d => d.classList.contains("pr")).length === W.whose.length);
page.setSection("whose");
ok("A chi tocca lists the user first and nobody last",
   /tu/.test($("content").querySelectorAll("span").filter(s => s.classList.contains("nm"))[0].textContent) &&
   /nessuno/.test($("content").textContent));
let copied = null;
navigator.clipboard.writeText = async t => { copied = t; };
const copyButton = acts("copy", $("content"))[0];
copyButton.click();
await settle();
ok("each person's chase is one click to copy", copied && copied.startsWith("@"), copied);

/* ---- 9. Mie: never approvable ---- */
page.setSection("mine");
ok("Mie opens on its first non-empty step", page.currentStep() === W.mine.first);
ok("Mie has no approve key", !/Approva/.test($("content").textContent));
const decide = rowsOf("mine", "decide");
globalThis.fetch = recording();
acts("loopone", $("content"))[0].click();
await settle();
ok("a decision without options goes to the chat as pr-loop on that PR",
   posted.some(p => p.path === "/api/run" && p.body.flow === "pr-loop" &&
                    p.body.items[0].n === decide[0].n && p.body.batch === 1));
posted = [];
const withOptions = JSON.parse(JSON.stringify(W));
withOptions.mine.steps.find(s => s.id === "decide").rows[0] =
  {...decide[0], ask: "Dividila in tre", options: ["Dividila", "Rispondi", "Lascia"]};
page.applyWizard(withOptions);
page.render();
acts("option", $("content"))[1].click();
await settle();
ok("an option is an order with exactly its text", posted.some(p =>
   p.path === `/api/pr/${decide[0].n}/order` && p.body.propose === "Rispondi"));
posted = []; globalThis.fetch = offline;
page.applyWizard(W);

/* ---- 10. Issue: close, let Claude do, decide ---- */
page.setSection("issue");
ok("Issue opens on Da chiudere", page.currentStep() === "close");
const closing = rowsOf("issue", "close");
globalThis.fetch = recording();
await page.primary();
await settle();
const close = posted.find(p => p.path === "/api/close");
ok("Chiudi sends each issue with the comment shown",
   close && close.body.items.length === closing.length &&
   close.body.items[0].body === closing[0].body);
posted = [];
page.setStep("claude");
await page.primary();
await settle();
ok("Le fa Claude hands the checked issues to issue-loop as one batch",
   posted.some(p => p.path === "/api/run" && p.body.flow === "issue-loop" &&
                    p.body.batch === rowsOf("issue", "claude").length));
posted = []; globalThis.fetch = offline;

/* ---- 11. the status bar and the activity drawer ---- */
page.setSection("review");
page.setStep("approve");
ok("one status bar at the bottom says where the wizard stands",
   /DA RIVEDERE 1\/4/.test(text("sb")));
acts("drawer", $("sb"))[0].click();
ok("Attività opens only when asked, with the feed", !$("drawer").hidden &&
   /Attività/.test(text("drawer")));
acts("drawer", $("sb"))[0].click();
ok("and closes", $("drawer").hidden);

/* ---- 12. while the preparation reads, the page already works ---- */
const reading = JSON.parse(JSON.stringify(W));
const pendingRows = reading.review.steps.find(s => s.id === "doubt").rows.splice(0);
reading.review.pending = pendingRows.map(c => ({...c, chip: "legge…"}));
reading.prepare.pr = {...reading.prepare.pr, status: "running",
                      phrase: "preparo la review · 3 di 9 lette"};
reading.review.first = "approve";
page.applyWizard(reading);
page.setSection("review");
page.setStep("prepare");
ok("the preparation screen shows how far it is and what each row is doing",
   /3 di 9 lette/.test($("content").textContent) && /legge…/.test($("content").textContent) &&
   /approvabile/.test($("content").textContent));
page.applyWizard(W);

/* ---- 13. the theme follows the host, and keeps a choice ---- */
ok("colour tokens on :root, prefers-color-scheme and data-theme",
   /:root\{color-scheme:light/.test(html) && /@media \(prefers-color-scheme: dark\)\{:root:not\(\[data-theme="light"\]\)/.test(html) &&
   /:root\[data-theme="dark"\]/.test(html));
page.toggleTheme();
const first = document.documentElement.dataset.theme;
page.toggleTheme();
ok("the theme key flips between the two", ["dark", "light"].includes(first) &&
   document.documentElement.dataset.theme !== first);
ok("a narrow layout exists for a sidebar or half a screen", /@media \(max-width:760px\)/.test(html));
ok("nothing threw along the way", errors.length === 0, errors[0] && errors[0].message);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
