## 2026-04-05 brain validation JSON fallback noise

- Per the repo contract in `AGENTS.md`, kept the existing graceful-degradation policy for brain validation: malformed advisory output still falls back to allowing the math-approved trade rather than blocking penny execution.
- Hardened `brain.py` so validation now tolerates wrapper prose around one JSON object instead of failing on the first non-JSON character.
- Reclassified malformed validation payloads as handled advisory failures rather than generic runtime errors.
- Cleaned the operator-facing fallback reason so penny/live logs now state that the advisory was malformed and that the parity policy kept the trade eligible, instead of repeating raw JSON decode noise like `Brain validation error ... defaulting to trade`.
- Added focused regression coverage for wrapped JSON extraction and malformed-JSON parity fallback messaging in `tests/test_brain_provider_migration.py`.
