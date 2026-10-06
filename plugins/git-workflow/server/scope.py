"""What one desk looks at: a scope of one or more repositories.

A scope is a name and its members. Each member is a repository on one
service, with the local clone its write actions run in — or none, and then
the desk reads and analyzes it but never works in it:

    {"repo": "erpy/erpy-engine", "host": "hub.genro.com",
     "provider": "forgejo", "cwd": "/…/erpy-org/erpy-engine" | None}

Three ways to name one, and they add up:

    --repo [host/]owner/repo   one repository (repeatable); none at all and
                               the cwd's origin is the one, as it always was
    --org [host/]owner         every repository of the owner with an open
                               issue or PR, read from one provider search
    --folder DIR               every clone directly under DIR

Launched with none of them from a directory that is not a checkout but holds
checkouts (~/Development/erpy-org), the scope is that folder.

Clones are found, never configured: the cwd, its children, its parent's
children and those of --clones, matched to a member by their origin.
"""

import subprocess
from pathlib import Path

from providers.detect import parse_remote, provider_for, resolve


def _origin(path):
    try:
        out = subprocess.run(("git", "-C", str(path), "remote", "get-url", "origin"),
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode:
        return None
    try:
        return parse_remote(out.stdout.strip())
    except SystemExit:
        return None


def _is_checkout(path):
    # a folder the user cannot read holds no clone of theirs
    try:
        return (Path(path) / ".git").exists()
    except OSError:
        return False


def clones(*roots):
    """{(host, 'owner/repo'): path} for every checkout in or right under
    the given directories, first found wins."""
    found = {}
    for root in roots:
        if not root:
            continue
        root = Path(root).expanduser().resolve()
        try:
            children = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
        except OSError:
            children = []
        candidates = [root] + children
        for path in candidates:
            if not _is_checkout(path):
                continue
            origin = _origin(path)
            if origin:
                found.setdefault((origin[0], origin[1].lower()), str(path))
    return found


def _member(host, repo, provider, known):
    return {"repo": repo, "host": host, "provider": provider,
            "cwd": known.get((host, repo.lower()))}


def _split_owner(spec):
    return tuple(spec.split("/", 1)) if "/" in spec else (None, spec)


def _org_host(owner, known, cwd):
    for host, repo in known:
        if repo.split("/")[0] == owner.lower():
            return host
    origin = _origin(cwd) if _is_checkout(cwd) else None
    return origin[0] if origin else None


def build(repos=(), orgs=(), folder=None, clone_roots=(), provider=None,
          cwd=None, get_provider=None):
    """(name, members) for a CLI call. `get_provider(name, host)` is the
    factory a --org search goes through; tests hand in the fixture's."""
    cwd = Path(cwd or Path.cwd()).resolve()
    roots = [cwd, cwd.parent] + [Path(r) for r in clone_roots]
    if folder:
        roots.insert(0, Path(folder))
    if not (repos or orgs or folder) and not _is_checkout(cwd) and clones(cwd):
        folder = cwd
    known = clones(*roots) if (repos or orgs or folder) else {}
    members, names = [], []

    def add(member):
        if all(m["repo"].lower() != member["repo"].lower() for m in members):
            members.append(member)

    if folder:
        folder = Path(folder).expanduser().resolve()
        names.append(folder.name)
        for (host, _), path in sorted(clones(folder).items(), key=lambda kv: kv[1]):
            if Path(path) == folder:
                continue
            origin = _origin(path)
            add({"repo": origin[1], "host": host,
                 "provider": provider or provider_for(host), "cwd": path})
    for spec in orgs:
        host, owner = _split_owner(spec)
        host = (host or _org_host(owner, known, cwd) or "").lower() or None
        name = provider or provider_for(host)
        names.append(owner)
        for repo in get_provider(name, host).scope_repos(owner):
            add(_member(host, repo, name, known))
    for spec in repos:
        if spec.count("/") == 1 and not provider:
            spec = next(("%s/%s" % (host, spec) for host, repo in known
                         if repo == spec.lower()), spec)
        name, repo, host = resolve(spec, provider, cwd=str(cwd))
        names.append(repo)
        add(_member(host, repo, name, known))
    if not (repos or orgs or folder):
        name, repo, host = resolve(None, provider, cwd=str(cwd))
        names.append(repo)
        add(dict(_member(host, repo, name, known), cwd=str(cwd)))
    if not members:
        raise SystemExit("the scope %s has no repository with anything open"
                         % " + ".join(names))
    return " + ".join(names), members
