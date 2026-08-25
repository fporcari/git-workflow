"""The merge gate, read once and cached — not re-derived by a model.

pr-triage §3 asks four questions before any verdict is honest, and every one
of them is a plain API read:

    CAN THE USER land on this base      restrictions + enforce_admins + his
                                        own permission, together
    how many approvals clear it          required_approving_review_count
    do unresolved threads block it       required_conversation_resolution
    does an admin walk past all of it    enforce_admins + my own permission

`restrictions` answers CAPABILITY, never ownership. Who *should* merge a PR
is a house rule — on this user's team it is the author/assignee — and the
desk has no business overriding it from a protection setting. What the gate
settles is whether the merge is possible at all:

    listed in restrictions            -> can land
    admin and enforce_admins is off   -> can land (the restriction does not
                                         bind admins; say so, do not treat it
                                         as a reason to hand the PR away)
    neither                           -> cannot land, whatever the reviews say

The distinction is not academic. On one real repo `develop` is restricted to
the maintainer with `enforce_admins: false`, and `master` to the same person
with `enforce_admins: true` — so the same user can merge on the first and
genuinely cannot on the second. Reading the restriction alone gets both
wrong.

CODEOWNERS is parsed for its owner set only. Deciding which rule a given PR
matches needs that PR's changed paths, which is a diff read — that stays
with the model, and the gate says so via `per_path`.
"""

import base64
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


def _gh(*args, timeout=30):
    out = subprocess.run(("gh",) + args, capture_output=True, text=True, timeout=timeout)
    return (out.returncode, out.stdout)


def _json(*args):
    code, body = _gh(*args)
    if code or not body.strip():
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _protection(repo, branch):
    return _json("api", "repos/%s/branches/%s/protection" % (repo, branch))


def _codeowners(repo, branch):
    for path in CODEOWNERS_PATHS:
        got = _json("api", "repos/%s/contents/%s" % (repo, path), "-f", "ref=%s" % branch)
        if got and got.get("content"):
            return path, base64.b64decode(got["content"]).decode("utf-8", "replace")
    return None, None


def _permission(repo, me):
    got = _json("api", "repos/%s/collaborators/%s/permission" % (repo, me))
    return (got or {}).get("permission")


def parse_codeowners(text):
    """Return (owners, per_path). `owners` is every login/team named anywhere;
    `per_path` says whether the rules are path-specific — when they are, which
    owners apply to a PR depends on its diff, and only a diff read can say."""
    owners, patterns = set(), []
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern, names = parts[0], [p.lstrip("@") for p in parts[1:] if p.startswith("@")]
        patterns.append(pattern)
        owners.update(names)
    per_path = any(p not in ("*", "/*") for p in patterns)
    return sorted(owners), per_path


def read(repo, me, branch):
    """The gate for one base branch. Four reads, run together."""
    with ThreadPoolExecutor(max_workers=3) as pool:  # noqa: E501
        prot = pool.submit(_protection, repo, branch)
        cown = pool.submit(_codeowners, repo, branch)
        perm = pool.submit(_permission, repo, me)
        protection, (co_path, co_text), permission = prot.result(), cown.result(), perm.result()

    return _shape(protection, (co_path, co_text), permission, me, branch)


def _shape(protection, codeowners, permission, me, branch):
    co_path, co_text = codeowners
    if protection is None:
        # an unprotected base: CLEAN means nothing, and anyone with write lands
        return {"branch": branch, "protected": False, "approvals": 0,
                "codeowners_required": False, "codeowners_path": None,
                "owners": [], "per_path": False, "dismiss_stale": False,
                "conversation_resolution": False, "enforce_admins": False,
                "landers": None, "permission": permission,
                "can_land": permission in ("admin", "maintain", "write"),
                "as_admin": False}

    reviews = protection.get("required_pull_request_reviews") or {}
    restrictions = protection.get("restrictions")
    landers = None
    if restrictions is not None:
        landers = ([u["login"] for u in restrictions.get("users") or []]
                   + ["@" + t["slug"] for t in restrictions.get("teams") or []]
                   + ["app:" + a["slug"] for a in restrictions.get("apps") or []])
    owners, per_path = parse_codeowners(co_text)
    enforce_admins = bool((protection.get("enforce_admins") or {}).get("enabled"))
    is_admin = permission == "admin"
    listed = landers is None or me in (landers or [])
    # an admin is not bound by the restriction unless admins are enforced
    admin_pass = is_admin and not enforce_admins
    return {
        "branch": branch,
        "protected": True,
        "approvals": reviews.get("required_approving_review_count") or 0,
        "codeowners_required": bool(reviews.get("require_code_owner_reviews")),
        "codeowners_path": co_path,
        "owners": owners,
        "per_path": per_path,
        "dismiss_stale": bool(reviews.get("dismiss_stale_reviews")),
        "conversation_resolution": bool(
            (protection.get("required_conversation_resolution") or {}).get("enabled")),
        "enforce_admins": enforce_admins,
        "landers": landers,
        "permission": permission,
        # capability, not ownership: on the list, or an admin the
        # restriction does not bind
        "can_land": listed or admin_pass,
        # informational: he gets through, but the branch is somebody else's
        "as_admin": (not listed) and admin_pass,
    }


def read_all(repo, me, branches):
    """One gate per distinct base in the queue — usually one or two."""
    branches = [b for b in dict.fromkeys(branches) if b]
    with ThreadPoolExecutor(max_workers=max(1, len(branches))) as pool:
        gates = pool.map(lambda b: read(repo, me, b), branches)
    return {gate["branch"]: gate for gate in gates}


def notes(gate):
    """The one-liners worth showing next to a verdict, in the user's language."""
    out = []
    if not gate:
        return out
    if not gate["protected"]:
        out.append("base non protetta: CLEAN non significa nulla qui")
        return out
    if gate["landers"] is not None:
        who = ", ".join(gate["landers"]) or "nessuno"
        if gate.get("as_admin"):
            out.append("push su %s riservati a %s: tu ci arrivi come admin "
                       "(enforce_admins è off) — chi non è admin no"
                       % (gate["branch"], who))
        elif not gate["can_land"]:
            out.append("non puoi mergiare su %s: è riservato a %s e "
                       "enforce_admins è attivo" % (gate["branch"], who))
    if gate["codeowners_required"] and not gate["codeowners_path"]:
        out.append("richiede l'approvazione di un codeowner ma il file CODEOWNERS "
                   "non c'\u00e8 su %s: il requisito non ha proprietari da "
                   "soddisfare" % gate["branch"])
    elif gate["codeowners_required"] and gate["per_path"]:
        out.append("CODEOWNERS ha regole per path: chi può approvare dipende dai "
                   "file toccati (serve leggere il diff)")
    if gate["conversation_resolution"]:
        out.append("i thread aperti bloccano il merge da soli")
    if gate["dismiss_stale"]:
        out.append("ogni push azzera le approvazioni")
    return out
