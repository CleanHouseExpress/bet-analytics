from apps.api.app.providers.football_data_org import FootballDataOrgProvider
from apps.api.app.providers.registry import ProviderRegistry
from apps.api.app.providers.sportmonks import SportmonksProvider


def build_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("football-data", FootballDataOrgProvider)
    registry.register("sportmonks", SportmonksProvider)
    return registry
