from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

PROVIDER_TEAM_ALIAS_CATALOG_VERSION = "provider-team-aliases-v1"


class ProviderTeamAliasCatalogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderTeamAliasRule:
    provider: str
    canonical_name: str
    aliases: tuple[str, ...]
    external_ids: tuple[str, ...] = ()


PROVIDER_TEAM_ALIAS_RULES = (
    ProviderTeamAliasRule(
        provider="football-data",
        canonical_name="Atlético-MG",
        aliases=("Mineiro",),
    ),
    ProviderTeamAliasRule(
        provider="football-data",
        canonical_name="Athletico-PR",
        aliases=("Paranaense",),
    ),
    ProviderTeamAliasRule(
        provider="football-data",
        canonical_name="Remo",
        aliases=("Clube do Remo",),
    ),
)


def normalize_team_label(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode()
    )
    return re.sub(r"[^a-z0-9]+", "", ascii_value.lower())


def canonical_names_for_provider_team(
    *,
    provider: str,
    external_id: str,
    labels: tuple[str, ...],
) -> tuple[str, ...]:
    normalized_labels = {
        normalize_team_label(label)
        for label in labels
        if label and normalize_team_label(label)
    }
    matches: set[str] = set()

    for rule in PROVIDER_TEAM_ALIAS_RULES:
        if rule.provider != provider:
            continue

        external_id_match = external_id in rule.external_ids
        alias_match = bool(
            normalized_labels
            & {
                normalize_team_label(alias)
                for alias in rule.aliases
                if normalize_team_label(alias)
            }
        )
        if external_id_match or alias_match:
            matches.add(rule.canonical_name)

    if len(matches) > 1:
        raise ProviderTeamAliasCatalogError(
            "AMBIGUOUS_PROVIDER_TEAM_ALIAS:"
            f"provider={provider},external_id={external_id},"
            f"canonical_names={','.join(sorted(matches))}"
        )
    return tuple(sorted(matches))


def validate_provider_team_alias_catalog(
    rules: tuple[ProviderTeamAliasRule, ...] | None = None,
) -> None:
    rules = PROVIDER_TEAM_ALIAS_RULES if rules is None else rules
    alias_ownership: dict[tuple[str, str], str] = {}
    external_id_ownership: dict[tuple[str, str], str] = {}

    for rule in rules:
        provider = rule.provider.strip()
        canonical_name = rule.canonical_name.strip()
        if not provider or not canonical_name:
            raise ProviderTeamAliasCatalogError("INVALID_PROVIDER_TEAM_ALIAS_RULE")
        if not rule.aliases and not rule.external_ids:
            raise ProviderTeamAliasCatalogError("EMPTY_PROVIDER_TEAM_ALIAS_RULE")

        for alias in rule.aliases:
            normalized = normalize_team_label(alias)
            if not normalized:
                raise ProviderTeamAliasCatalogError("EMPTY_PROVIDER_TEAM_ALIAS")
            key = (provider, normalized)
            previous = alias_ownership.get(key)
            if previous is not None and previous != canonical_name:
                raise ProviderTeamAliasCatalogError(
                    "DUPLICATE_PROVIDER_TEAM_ALIAS:"
                    f"provider={provider},alias={alias},"
                    f"canonical_names={previous},{canonical_name}"
                )
            alias_ownership[key] = canonical_name

        for external_id in rule.external_ids:
            normalized_external_id = external_id.strip()
            if not normalized_external_id:
                raise ProviderTeamAliasCatalogError(
                    "EMPTY_PROVIDER_TEAM_EXTERNAL_ID"
                )
            key = (provider, normalized_external_id)
            previous = external_id_ownership.get(key)
            if previous is not None and previous != canonical_name:
                raise ProviderTeamAliasCatalogError(
                    "DUPLICATE_PROVIDER_TEAM_EXTERNAL_ID:"
                    f"provider={provider},external_id={normalized_external_id},"
                    f"canonical_names={previous},{canonical_name}"
                )
            external_id_ownership[key] = canonical_name


validate_provider_team_alias_catalog()
