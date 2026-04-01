# 2026-04-01 Trade State Architecture Review

## Chosen Architecture
- `paper_research`
  - Purpose: research simulation only.
  - Source of truth: local SQLite trade row plus snapshots/signals.
  - Required identifiers: local trade id, strategy linkage (`signal_id` or `weather_signal_id`), token ids, entry/exit prices, size, labels.
  - Explicitly does not depend on exchange order ids or wallet position identity.
- `wallet_attached`
  - Purpose: paper mirror of a watched wallet, where lifecycle depends on an external source position.
  - Source of truth: canonical wallet-position identity plus live wallet polling.
  - Required identifiers: `copy_wallet`, `copy_condition_id`, `copy_outcome`, `canonical_ref`, and `external_position_id`.
- `live_exchange`
  - Purpose: wallet-attached live execution.
  - Source of truth: exchange order ids and exchange status checks.
  - Required identifiers: `external_order_id_a`, `external_order_id_b`, `canonical_ref`.

## Why This Split
- Paper research should be cheap to reason about. It only needs local accounting and mark-to-market inputs, so storing fake filled orders was unnecessary complexity.
- Wallet-linked states need identity guarantees that survive polling, restarts, and reconciliation. A plain `(wallet, conditionId)` key was too weak because YES and NO positions on the same market collapse together.
- Live trades need explicit exchange-linked identifiers. Without them, there is no safe canonical reconciliation path.

## Tradeoffs
- Canonical wallet identity now uses `wallet + conditionId + outcome`, while the raw token/asset id is stored separately as `external_position_id`.
- This is slightly more verbose than a single field, but it is more robust operationally:
  - condition/outcome survives payloads that omit `asset`
  - raw asset id is still persisted when present for stronger linkage and auditing
- Existing rows are backfilled best-effort through migration `012_trade_state_modes`, but only newly opened trades are guaranteed to have the full normalized shape from the start.

## Review Notes
- The remaining reconciliation path for live exchange trades is still order-centric, not fill-ledger-centric. That is acceptable for this cleanup because the canonical order ids are now persisted explicitly, which was the prerequisite missing piece.
- Copy-trade monitoring, the `/api/copy/positions` mirror view, and autonomy now all share the same reconciliation key logic. That removes a class of drift bugs where one path mirrored or closed by condition id while another path reasoned by wallet state differently.
