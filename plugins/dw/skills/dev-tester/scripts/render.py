#!/usr/bin/env python3
"""Render a collaudo content JSON into a standalone HTML page the tester fills in.

    render.py content.json -o collaudo.html

The page records OK/KO and a note per step, keeps the answers in the tester's
browser, and exports them as JSON (schema `collaudo/1`, read back by esito.py).
"""
import argparse
import html
import json
import pathlib
import re
import sys

SKILL_DIR = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_DIR / "assets" / "template.html"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK = re.compile(r"\[([^\]]+)\]\(((?:https?://|#)[^)\s]+)\)")
CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*")
STATE_KINDS = ("done", "wait", "ko")

LABELS = {
    "it": {
        "filled_by": "Compilato da",
        "filled_by_placeholder": "Nome e cognome",
        "steps": "passi",
        "export": "Esporta risposte (JSON)",
        "copy": "Copia JSON",
        "fill_help": "Per ogni passo scegliere OK o KO e, se serve, scrivere una nota. "
                     "Le risposte restano salvate in questo browser. A fine prova premere "
                     "«Esporta risposte (JSON)» e mandare il file.",
        "summary_case": "Segnalazione",
        "summary_where": "Istanza e caso reale",
        "summary_fix": "Correzione",
        "open_question": "Domanda aperta:",
        "checklist": "Checklist",
        "col_n": "#",
        "col_step": "Passo",
        "col_expected": "Risultato atteso",
        "col_result": "Esito",
        "modifies_data": "modifica dati",
        "watch": "Da tenere d'occhio",
        "report": "Come riportare l'esito",
        "report_default": "Per un KO scrivere nella nota quello che si è visto: il messaggio esatto "
                          "ed eventuali codici di errore. Gli screenshot vanno mandati a parte, "
                          "citando il numero del passo.",
        "report_modifying": "Questi passi modificano dati reali: {steps}. Se un passo precedente "
                            "dello stesso caso è KO, fermarsi lì.",
        "step_ref": "{case}, passo {n}",
        "general_notes": "Note generali",
        "general_placeholder": "Osservazioni che non riguardano un passo preciso",
        "references": "Riferimenti",
        "ref_case": "Caso",
        "ref_issue": "Issue",
        "ref_fix": "Correzione",
        "ref_state": "Stato",
        "js": {
            "result": "Esito passo",
            "note": "Nota",
            "exported": "Esportato {file} ({done}/{total} passi compilati).",
            "missing_name": "Manca il nome in «Compilato da».",
            "download_failed": "Il download non è riuscito: usare «Copia JSON» e incollare il testo in un messaggio.",
            "copied": "JSON copiato negli appunti.",
            "copy_failed": "Copia non riuscita: usare «Esporta risposte (JSON)».",
        },
    },
    "en": {
        "filled_by": "Filled in by",
        "filled_by_placeholder": "Full name",
        "steps": "steps",
        "export": "Export answers (JSON)",
        "copy": "Copy JSON",
        "fill_help": "For each step pick OK or KO and add a note when useful. The answers stay "
                     "saved in this browser. When done, press “Export answers (JSON)” and send "
                     "the file.",
        "summary_case": "Report",
        "summary_where": "Environment and real case",
        "summary_fix": "Fix",
        "open_question": "Open question:",
        "checklist": "Checklist",
        "col_n": "#",
        "col_step": "Step",
        "col_expected": "Expected result",
        "col_result": "Result",
        "modifies_data": "changes data",
        "watch": "Watch out for",
        "report": "How to report the result",
        "report_default": "For a KO, write in the note what you saw: the exact message and any "
                          "error code. Send screenshots separately, quoting the step number.",
        "report_modifying": "These steps change real data: {steps}. If an earlier step of the "
                            "same case is KO, stop there.",
        "step_ref": "{case}, step {n}",
        "general_notes": "General notes",
        "general_placeholder": "Remarks that are not about one step",
        "references": "References",
        "ref_case": "Case",
        "ref_issue": "Issue",
        "ref_fix": "Fix",
        "ref_state": "State",
        "js": {
            "result": "Result of step",
            "note": "Note",
            "exported": "Exported {file} ({done}/{total} steps filled in).",
            "missing_name": "“Filled in by” is empty.",
            "download_failed": "The download did not start: use “Copy JSON” and paste the text in a message.",
            "copied": "JSON copied to the clipboard.",
            "copy_failed": "Copy failed: use “Export answers (JSON)”.",
        },
    },
}


class ContentError(ValueError):
    pass


def inline(text):
    """Escape, then allow `code`, **bold** and [label](https://… or #anchor)."""
    out = html.escape(str(text), quote=True)
    out = CODE.sub(r"<code>\1</code>", out)
    out = BOLD.sub(r"<strong>\1</strong>", out)
    return LINK.sub(r'<a href="\2">\1</a>', out)


def link(ref):
    if isinstance(ref, str):
        return inline(ref)
    label = html.escape(ref["label"])
    url = ref.get("url")
    if url and re.match(r"^(?:https?://|#)", url):
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'
    return label


def links(refs):
    return " + ".join(link(r) for r in refs or []) or "—"


def blocks(items):
    parts = []
    for item in items or []:
        if isinstance(item, dict) and "list" in item:
            parts.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in item["list"]) + "</ul>")
        else:
            parts.append(f"<p>{inline(item)}</p>")
    return "\n".join(parts)


def state_cell(state):
    if not state:
        return "—"
    kind = state.get("kind", "done")
    kind = kind if kind in STATE_KINDS else "done"
    return f'<span class="state {kind}">{html.escape(state["text"])}</span>'


def ref_labels(case):
    return [r["label"] if isinstance(r, dict) else str(r)
            for r in (case.get("issues") or []) + (case.get("fixes") or [])]


def validate(content):
    for key in ("id", "title", "cases"):
        if not content.get(key):
            raise ContentError(f"missing top-level `{key}`")
    if not SLUG.match(content["id"]):
        raise ContentError(f"`id` must be a lowercase slug, got {content['id']!r}")
    lang = content.get("lang", "it")
    if lang not in LABELS:
        raise ContentError(f"`lang` must be one of {sorted(LABELS)}, got {lang!r}")
    seen = set()
    for case in content["cases"]:
        cid = case.get("id", "")
        if not SLUG.match(cid):
            raise ContentError(f"case id must be a lowercase slug, got {cid!r}")
        if cid in seen:
            raise ContentError(f"duplicate case id {cid!r}")
        seen.add(cid)
        if not case.get("title"):
            raise ContentError(f"case {cid!r} has no `title`")
        if not case.get("steps"):
            raise ContentError(f"case {cid!r} has no `steps`")
        for n, step in enumerate(case["steps"], 1):
            if not step.get("do") or not step.get("expect"):
                raise ContentError(f"case {cid!r} step {n} needs both `do` and `expect`")
    return lang


def render_body(content, lab):
    cases = content["cases"]
    out = ['<header class="top">']
    if content.get("eyebrow"):
        out.append(f'<p class="eyebrow">{inline(content["eyebrow"])}</p>')
    out.append(f"<h1>{inline(content['title'])}</h1>")
    if content.get("meta"):
        out.append(f'<p class="meta">{inline(content["meta"])}</p>')
    out.append("</header>")

    out.append(f"""<form class="fill" id="fill" autocomplete="off">
  <label class="who" for="filled-by">{lab['filled_by']}
    <input type="text" id="filled-by" name="filled-by" placeholder="{lab['filled_by_placeholder']}"></label>
  <div class="progress" aria-live="polite">
    <span><b id="p-done">0</b>/<span id="p-tot">0</span> {lab['steps']}</span>
    <span class="k-ok">OK <b id="p-ok">0</b></span>
    <span class="k-ko">KO <b id="p-ko">0</b></span>
  </div>
  <div class="actions">
    <button type="button" class="primary" id="btn-export">{lab['export']}</button>
    <button type="button" class="secondary" id="btn-copy">{lab['copy']}</button>
  </div>
  <p class="toast" id="toast" role="status"></p>
</form>""")

    out.append("<section>")
    if content.get("lead"):
        out.append(f'<p class="lead">{inline(content["lead"])}</p>')
    out.append(f"""<div class="tablewrap"><table>
<thead><tr><th>{lab['summary_case']}</th><th>{lab['summary_where']}</th><th>{lab['summary_fix']}</th></tr></thead>
<tbody>""")
    for case in cases:
        where = " · ".join(inline(x) for x in (case.get("instance"), case.get("real_case")) if x) or "—"
        out.append(f'<tr><td><a href="#{case["id"]}">{inline(case["title"])}</a></td>'
                   f"<td>{where}</td><td>{links(case.get('fixes'))}</td></tr>")
    out.append("</tbody></table></div>")
    out.append(f"<p>{lab['fill_help']}</p>")
    for question in content.get("open_questions") or []:
        out.append(f'<div class="open"><strong>{lab["open_question"]}</strong> {inline(question)}</div>')
    out.append("</section>")

    modifying = []
    for case in cases:
        cid = case["id"]
        refs = html.escape(",".join(ref_labels(case)), quote=True)
        inst = html.escape(case.get("instance") or "", quote=True)
        out.append(f'<section id="{cid}" data-case="{cid}" data-instance="{inst}" data-refs="{refs}">')
        chip = f' <span class="inst">{inline(case["instance"])}</span>' if case.get("instance") else ""
        out.append(f"<h2>{inline(case['title'])}{chip}</h2>")
        if case.get("answer"):
            out.append(f'<div class="answer">{blocks(case["answer"])}</div>')
        out.append(f"<h3>{lab['checklist']}</h3>")
        out.append(f"""<div class="tablewrap"><table>
<thead><tr><th>{lab['col_n']}</th><th>{lab['col_step']}</th><th>{lab['col_expected']}</th><th>{lab['col_result']}</th></tr></thead>
<tbody>""")
        for n, step in enumerate(case["steps"], 1):
            mod = bool(step.get("modifies_data"))
            if mod:
                modifying.append(lab["step_ref"].format(case=case["title"], n=n))
            badge = f'<br><span class="chip">{lab["modifies_data"]}</span>' if mod else ""
            out.append(f'<tr data-step="{cid}-{n}" data-modifies="{str(mod).lower()}">'
                       f'<td class="n">{n}</td><td class="step">{inline(step["do"])}{badge}</td>'
                       f'<td class="exp">{inline(step["expect"])}</td><td class="esito"></td></tr>')
        out.append("</tbody></table></div>")
        if case.get("watch"):
            items = "".join(f"<li>{inline(w)}</li>" for w in case["watch"])
            out.append(f'<div class="watch"><h3>{lab["watch"]}</h3><ul>{items}</ul></div>')
        out.append("</section>")

    out.append(f'<section id="report"><h2>{lab["report"]}</h2>')
    out.append(blocks(content.get("report") or [lab["report_default"]]))
    if modifying:
        out.append(f"<p>{html.escape(lab['report_modifying'].format(steps='; '.join(modifying)))}</p>")
    out.append(f'<h3><label for="general-notes">{lab["general_notes"]}</label></h3>'
               f'<textarea id="general-notes" name="general-notes" placeholder="{lab["general_placeholder"]}"></textarea>')
    out.append("</section>")

    rows = [(c["title"], c.get("issues"), c.get("fixes"), c.get("state")) for c in cases]
    rows += [(r["title"], r.get("issues"), r.get("fixes"), r.get("state"))
             for r in content.get("extra_references") or []]
    if any(r[1] or r[2] for r in rows):
        out.append(f"""<section id="references"><h2>{lab['references']}</h2><div class="tablewrap"><table>
<thead><tr><th>{lab['ref_case']}</th><th>{lab['ref_issue']}</th><th>{lab['ref_fix']}</th><th>{lab['ref_state']}</th></tr></thead>
<tbody>""")
        for title, issues, fixes, state in rows:
            out.append(f"<tr><td>{inline(title)}</td><td>{links(issues)}</td>"
                       f"<td>{links(fixes)}</td><td>{state_cell(state)}</td></tr>")
        out.append("</tbody></table></div></section>")

    if content.get("footer"):
        out.append(f"<footer>{inline(content['footer'])}</footer>")
    return "\n".join(out)


def render(content):
    lang = validate(content)
    lab = LABELS[lang]
    ui = json.dumps(lab["js"], ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8")
    for token, value in (
        ("__LANG__", lang),
        ("__TITLE__", html.escape(content["title"])),
        ("__DOC_ID__", content["id"]),
        ("__DOC_VERSION__", str(int(content.get("version", 1)))),
        ("__UI__", ui),
    ):
        page = page.replace(token, value)
    return page.replace("__BODY__", render_body(content, lab))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("content", type=pathlib.Path, help="collaudo content JSON")
    parser.add_argument("-o", "--output", type=pathlib.Path, required=True, help="HTML file to write")
    args = parser.parse_args()
    try:
        content = json.loads(args.content.read_text(encoding="utf-8"))
        page = render(content)
    except (OSError, json.JSONDecodeError, ContentError, KeyError) as e:
        print(f"render.py: {e}", file=sys.stderr)
        return 1
    args.output.write_text(page, encoding="utf-8")
    steps = sum(len(c["steps"]) for c in content["cases"])
    print(f"{args.output} · {len(content['cases'])} cases · {steps} steps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
