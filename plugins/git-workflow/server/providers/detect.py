"""Which service a checkout talks to, read from its `origin` remote.

The host decides the provider: each class in PROVIDERS names the hosts it
serves (github.com for GitHub, the host of FORGEJO_URL for Forgejo), anything
else is an error. Never a default — a GitHub read against a Forgejo checkout
does not fail, it returns an empty queue, and an empty queue reads as
"nothing to do". A new service is one class here and one entry in PROVIDERS.
"""

import re
import subprocess
from urllib.parse import urlparse

from .fixture import FixtureProvider
from .forgejo import ForgejoProvider
from .github import GitHubProvider

PROVIDERS = {cls.name: cls for cls in (GitHubProvider, ForgejoProvider, FixtureProvider)}

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


def provider_for(host):
    for name, cls in PROVIDERS.items():
        if host and host in cls.hosts():
            return name
    hint = ("set FORGEJO_URL=https://%s and FORGEJO_TOKEN if it is a Forgejo"
            % host) if host else "no host"
    raise SystemExit("unknown git host %r: %s" % (host, hint))


def resolve(repo=None, provider=None, cwd=None):
    """(provider name, 'owner/repo', host) for a CLI call.

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
    name = provider or provider_for(host)
    if not host and name in PROVIDERS:
        host = next(iter(PROVIDERS[name].hosts()), None)
    return name, repo, host


def get_provider(name, host=None):
    try:
        cls = PROVIDERS[name]
    except KeyError:
        raise SystemExit("unknown provider %r (available: %s)" % (name, ", ".join(PROVIDERS)))
    return cls(host=host)


def provider_and_repo(args):
    """The provider and repo a CLI call addresses, from --repo/--provider and
    the origin. The default is never one service: it is the origin's host."""
    name, repo, host = resolve(args.repo, args.provider)
    return get_provider(name, host=host), repo
