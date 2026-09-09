from collections.abc import Callable

from apps.api.app.providers.base import FootballDataProvider

ProviderFactory = Callable[[], FootballDataProvider]


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, name: str, factory: ProviderFactory) -> None:
        key = name.strip().lower()
        if not key:
            raise ValueError("Provider name cannot be empty")
        self._factories[key] = factory

    def create(self, name: str) -> FootballDataProvider:
        key = name.strip().lower()
        try:
            return self._factories[key]()
        except KeyError as exc:
            available = ", ".join(sorted(self._factories)) or "none"
            raise ValueError(f"Unknown provider '{name}'. Available: {available}") from exc

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))
