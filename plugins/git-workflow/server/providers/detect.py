"""Which service a checkout talks to, read from its `origin` remote.

The host decides the provider: github.com is GitHub, the host of FORGEJO_URL
is Forgejo, anything else is an error. Never a default — a GitHub read against
a Forgejo checkout does not fail, it returns an empty queue, and an empty
queue reads as "nothing to do".
"""

import os
import re
import subprocess
from urllib.parse import urlparse

SCP_LIKE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+)$")


def origin_url(cwd=None):
    out = subprocess.run(("git", "remote", "get-url", "origin"),
                         capture_output=True, text=True, cwd=cwd)
    if out.returncode:
        raise SystemExit("no --repo given and no git origin in the current directory")
    return out.stdout.strip()


def parse_remote(url):
    """(host, 'owner/repo') from any of the remote URL forms git accepts."""
    if "://" in url:
        parsed = urlparse(url)
        host, path = parsed.hostname or "", parsed.path
    else:
        match = SCP_LIKE.match(url)
        if not match:
            raise SystemExit("unrecognized remote URL: %s" % url)
        host, path = match.group("host"), match.group("path")
    repo = path.strip("/").removesuffix(".git")
    if repo.count("/") != 1:
        raise SystemExit("remote %s does not name an owner/repo" % url)
    return host.lower(), repo


def forgejo_host():
    base = os.environ.get("FORGEJO_URL", "")
    return (urlparse(base).hostname or "").lower() if base else ""


def provider_for(host):
    if host == "github.com":
        return "github"
    if host and host == forgejo_host():
        return "forgejo"
    hint = ("set FORGEJO_URL=https://%s and FORGEJO_TOKEN if it is a Forgejo"
            % host) if host else "no host"
    raise SystemExit("unknown git host %r: %s" % (host, hint))


def resolve(repo=None, provider=None, cwd=None):
    """(provider name, 'owner/repo') for a CLI call.

    --repo may carry a host prefix (host/owner/repo); without one the host is
    the origin's. --provider forces the name, fixture included.
    """
    host = None
    if repo and repo.count("/") == 2:
        host, repo = repo.split("/", 1)
        host = host.lower()
    if not repo or (not provider and not host):
        origin_host, origin_repo = parse_remote(origin_url(cwd))
        repo = repo or origin_repo
        host = host or origin_host
    return provider or provider_for(host), repo
