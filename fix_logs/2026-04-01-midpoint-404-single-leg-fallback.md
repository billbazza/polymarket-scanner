# 2026-04-01 Midpoint 404 Single-Leg Fallback

## Issue

Server logs on 2026-04-01 showed repeated `/midpoint` 404 warnings for open single-leg trades, including:

- trade `339` token `62912270282573897414972102236697220479291421322252061858632494742341024783821`
- trade `342` token `32183321218413428273944559955600104592336664441538191747256593804334779812732`
- trade `343` token `93513184965401077523870915204036795077500901899496211902962442588353866245078`
- trade `344` token `15874049785179046400166045999091331306561970280089363772684483185257910124385`

## Root Cause

- The warning path was not weather-specific. `tracker.py` routes `weather`, `copy`, and `whale` trades through the same single-leg refresh function.
- The affected examples were open `copy` trades, not `weather` trades.
- CLOB `/midpoint` returns `404 {"error":"No orderbook exists for the requested token id"}` after some markets resolve or otherwise lose an active orderbook.
- Gamma still exposes the market metadata and final `outcomePrices` for those markets. For the example trades above, Gamma reports `umaResolutionStatus="resolved"` and `outcomePrices=["0","1"]`.
- `db.close_trade()` only treated `weather` as single-leg for P&L, so even if copy/whale trades were closed from a resolved token price, realized P&L would have been wrong.

## Fix

- Added Gamma market lookup helpers in `api.py` for `condition_ids`, `clob_token_ids`, and market `id`.
- Added shared single-leg price resolution in `tracker.py`:
  - try CLOB midpoint first
  - on midpoint `404`, fetch the market from Gamma
  - use Gamma `outcomePrices` for the matching token when available
  - treat resolved Gamma markets as terminal and auto-close `copy`/`whale`
  - for weather, keep unresolved/unpriceable post-target-date trades open with an explicit tracker note instead of warning every cycle
- Added warning/info throttling so the same midpoint-404 condition does not spam logs every refresh loop.
- Added persistent tracker notes on open trades for missing provenance or awaiting-resolution states.
- Updated `db.close_trade()` so `copy` and `whale` use the same single-leg P&L math as `weather`.
- Added direct `db.get_weather_signal_by_id()` lookup so older weather trades retain provenance without scanning only recent rows.

## Verification

- `python3 test_all.py`
  - New regression coverage:
    - resolved copy trade closes via Gamma fallback after midpoint 404
    - past-due weather trade with no usable fallback price stays open and is marked as awaiting resolution
  - Current suite result: `172/173` passing
  - Remaining failure is pre-existing and unrelated: autonomy test expects `paper max_open = 100`, repo currently has `25`

## Notes

- As of 2026-04-01, the live `scanner.db` had `18` open single-leg trades whose stored token returned midpoint `404`.
- The sampled problem trades (`339`, `342`, `343`, `344`, `350`, `351`, `352`, `353`) all mapped to Gamma markets already marked `resolved` with `outcomePrices=["0","1"]`.

## 2026-04-01 follow-up: invalid whale token fallback

- Server logs on 2026-04-01 also showed whale trade `282` repeatedly hitting midpoint `404`, then Gamma lookup `422`, because the stored `token_id_a` was an invalid placeholder (`test_token_id`) rather than a real CLOB token id.
- Added shared token-id sanitizing in `api.py` and reused it in `whale_detector.py` so new whale alerts and whale trades do not persist placeholder token ids into `trades.token_id_a`.
- Hardened `tracker.py` single-leg pricing so malformed token ids are classified as `invalid-token`, skip both midpoint and Gamma lookups, write a persistent tracker note, and leave the trade open without repeated warning spam.
- Tightened the no-fallback path so missing Gamma matches and missing Gamma outcome prices are recorded as explicit unpriceable states instead of recurring warnings.
- Added targeted regression tests for:
  - whale trade creation with invalid token input
  - existing malformed whale trades staying open without midpoint or Gamma calls
