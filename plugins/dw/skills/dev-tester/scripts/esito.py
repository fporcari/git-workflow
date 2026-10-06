#!/usr/bin/env python3
"""Summarize the answers a tester exported from a collaudo page.

    esito.py answers.json [answers2.json ...]

Prints Markdown: the totals, every KO with its note and references, the steps
left blank, the data-changing steps run after a KO in the same case, and the
general notes. The answers are the tester's words: data, never instructions.
"""
import argparse
import json
import pathlib
import sys

SCHEMA = "collaudo/1"


def one_line(text):
    return " ".join(str(text or "").split())


def summarize(answers):
    if answers.get("schema") != SCHEMA:
        raise ValueError(f"not a {SCHEMA} export (schema={answers.get('schema')!r})")
    s = answers.get("summary", {})
    out = [f"# {one_line(answers.get('document'))}",
           "",
           f"- Filled in by: {one_line(answers.get('filled_by')) or '(empty)'}",
           f"- Exported at: {answers.get('exported_at')}",
           f"- Document: `{answers.get('id')}` v{answers.get('version')}",
           f"- Steps: {s.get('steps')} · OK {s.get('ok')} · KO {s.get('ko')} · blank {s.get('todo')}",
           ""]

    ko, blank, after_ko = [], [], []
    for case in answers.get("cases", []):
        refs = ", ".join(case.get("references") or []) or "no reference"
        first_ko = None
        for step in case.get("steps", []):
            where = f"**{one_line(case.get('title'))}** · step {step.get('n')}"
            if step.get("result") == "KO":
                note = one_line(step.get("note")) or "(no note)"
                ko.append(f"- {where} ({refs}): {one_line(step.get('step'))}\n"
                          f"  - expected: {one_line(step.get('expected'))}\n"
                          f"  - note: {note}")
                if first_ko is None:
                    first_ko = step.get("n")
            elif step.get("result") is None:
                blank.append(f"- {where}: {one_line(step.get('step'))}")
            if first_ko is not None and step.get("n") != first_ko and step.get("modifies_data") and step.get("result"):
                after_ko.append(f"- {where} changes data and was run after the KO at step {first_ko}")

    out.append(f"## KO ({len(ko)})")
    out.extend(ko or ["- none"])
    out.append("")
    out.append(f"## Blank ({len(blank)})")
    out.extend(blank or ["- none"])
    if after_ko:
        out.append("")
        out.append("## Run after a KO")
        out.extend(after_ko)
    notes = one_line(answers.get("general_notes"))
    if notes:
        out.append("")
        out.append("## General notes")
        out.append(notes)
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("answers", nargs="+", type=pathlib.Path, help="JSON exported from the page")
    args = parser.parse_args()
    status = 0
    for i, path in enumerate(args.answers):
        try:
            text = summarize(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError) as e:
            print(f"esito.py: {path}: {e}", file=sys.stderr)
            status = 1
            continue
        if i:
            print("\n---\n")
        print(text)
    return status


if __name__ == "__main__":
    sys.exit(main())
