from .github import GitHubProvider
from .forgejo import ForgejoProvider
from .fixture import FixtureProvider

PROVIDERS = {
    "github": GitHubProvider,
    "forgejo": ForgejoProvider,
    "fixture": FixtureProvider,
}


def get_provider(name, host=None):
    """The provider by name; `host` is the origin's, so a Forgejo provider
    can address the instance a checkout actually points at."""
    try:
        cls = PROVIDERS[name]
    except KeyError:
        raise SystemExit("unknown provider %r (available: %s)" % (name, ", ".join(PROVIDERS)))
    if name == "forgejo" and host:
        return cls(base="https://%s" % host)
    return cls()
