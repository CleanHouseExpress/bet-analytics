# Provider team names and aliases

## Decision

Provider team reconciliation uses three layers of evidence:

1. the provider's full team `name`;
2. non-canonical provider aliases exposed by the adapter, such as football-data.org
   `shortName`;
3. the versioned provider-specific catalog in
   `apps/api/app/providers/team_aliases.py`.

For football-data.org, `name` is the primary textual identity. `shortName` is
carried only as reconciliation evidence. The `tla` value stays in the raw
provider payload and is not used automatically because three-letter abbreviations
are too lossy to be a safe canonical identity.

## Why provider aliases are not copied into team_aliases

`team_aliases` is a global canonical alias table. Provider-only labels such as
`Mineiro` or `Paranaense` can be context-dependent, so copying them into the
global table would make one provider's vocabulary affect every other provider.

Known provider labels therefore stay in the source-controlled catalog. Once a
provider team resolves to exactly one canonical team, the normal
`external_entity_mappings` row is persisted for `provider + external_id`.
Subsequent synchronizations use that external identity directly.

## Fail-closed behavior

Strict synchronization never creates a team when reconciliation cannot prove a
single canonical identity.

A provider team is blocked when:

- no canonical name, canonical alias, or versioned provider rule matches;
- provider evidence points to more than one canonical team;
- a persisted external mapping conflicts with a versioned canonical rule.

The operational sync remains strict by default. `--allow-create-teams` is an
explicit escape hatch. When creation is explicitly allowed and a versioned rule
exists, the canonical name from the catalog is used instead of the provider label.

## Adding a new alias or provider

1. Add or update a `ProviderTeamAliasRule`.
2. Keep the provider name equal to the registered provider name.
3. Point the rule to the canonical team name used by the application.
4. Prefer full provider names and provider-specific aliases. Add external IDs when
   they are stable and verified.
5. Bump `PROVIDER_TEAM_ALIAS_CATALOG_VERSION` whenever reconciliation semantics
   change.
6. Add tests for the known alias and at least one conflicting/unknown case.
7. Run the full CI before deploying.

The catalog validates on import and rejects an alias owned by two different
canonical teams for the same provider.
