from .github import GitHubProvider
from .forgejo import ForgejoProvider
from .fixture import FixtureProvider

PROVIDERS = {
    "github": GitHubProvider,
    "forgejo": ForgejoProvider,
    "fixture": FixtureProvider,
}


def get_provider(name):
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise SystemExit("unknown provider %r (available: %s)" % (name, ", ".join(PROVIDERS)))
