from .github import GitHubProvider
from .forgejo import ForgejoProvider

PROVIDERS = {
    "github": GitHubProvider,
    "forgejo": ForgejoProvider,
}


def get_provider(name):
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise SystemExit("unknown provider %r (available: %s)" % (name, ", ".join(PROVIDERS)))
