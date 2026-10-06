#!/usr/bin/env python3
"""Render a release checklist JSON into the standalone HTML page the reviewer fills in.

    render.py checklist.json -o dep-merger.html

The page keeps the ticks in the reviewer's browser and exports the cherry-pick list.
"""
import argparse
import json
import pathlib

SKILL_DIR = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_DIR / "assets" / "template.html"
UNIT_KEYS = ("id", "label", "author", "title", "picks")
TOP_KEYS = ("repo", "base", "head", "remote", "branch", "title", "order", "units")


def validate(data):
    missing = [k for k in TOP_KEYS if k not in data]
    if missing:
        raise SystemExit(f"checklist: missing {missing}")
    ids = [u.get("id") for u in data["units"]]
    if len(ids) != len(set(ids)):
        raise SystemExit("checklist: duplicate unit ids")
    order = set(data["order"])
    for u in data["units"]:
        missing = [k for k in UNIT_KEYS if k not in u]
        if missing:
            raise SystemExit(f"unit {u.get('id')}: missing {missing}")
        for r in u.get("requires", []):
            if r not in ids:
                raise SystemExit(f"unit {u['id']}: requires unknown unit {r}")
        options = {o["key"] for o in u.get("options", [])}
        for p in u["picks"]:
            if p["sha"] not in order:
                raise SystemExit(f"unit {u['id']}: {p['sha']} is not in order")
            if p.get("opt") and p["opt"] not in options:
                raise SystemExit(f"unit {u['id']}: pick {p['sha'][:8]} names unknown option {p['opt']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("checklist")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    data = json.loads(pathlib.Path(a.checklist).read_text(encoding="utf-8"))
    validate(data)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8")
    page = page.replace("__TITLE__", data["title"]).replace("/*__DATA__*/null", payload)
    pathlib.Path(a.out).write_text(page, encoding="utf-8")
    print(a.out)


if __name__ == "__main__":
    main()
