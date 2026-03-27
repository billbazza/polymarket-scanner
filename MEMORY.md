# Polymarket Scanner — Memory

## Decisions

- **Quarter-Kelly, not half or full** — Full Kelly has too much variance for real trading. Quarter-Kelly lets you survive bad runs. The article author confirmed this after losing sleep over half-Kelly swings.

- **GTC orders over FOK** — Fill-or-Kill orders fail ~40% of the time on thin Polymarket books. GTC orders sit and wait, achieving ~95% fill rate. Implemented in execution.py.

- **Cointegration threshold p<0.10** — Looser than academic standard (0.05) because Polymarket pairs have shorter history. Use `--strict` flag for p<0.05 when you want higher confidence.

- **Max 15 markets per event** — Events with 100+ markets (sports, elections) create O(n^2) pair explosion. Cap at 15 to keep scan times reasonable.

- **Claude Haiku for brain, not Sonnet** — Cost efficiency. At ~$0.001 per question, a full scan batch costs ~$0.02. Sonnet would be 10x more for marginal accuracy gain on probability estimation.

- **Single-page dashboard** — All HTML/CSS/JS in one file. No build step, no npm, no React. Serves directly from FastAPI. Trade-off: harder to maintain at scale, but zero deployment complexity.

- **LaunchAgent over crontab** — macOS crontab requires Full Disk Access permission which is annoying to grant. LaunchAgent works natively.

## Gotchas

- **Gamma API rejects `order` parameter** — Don't pass `order` to event queries. Returns 422.

- **`eval()` was used for JSON parsing in early version** — Fixed to `json.loads()`. Never reintroduce `eval()`.

- **Price history alignment** — Two tokens from the same event don't always have matching timestamps. The scanner falls back to index-based alignment when <20 common timestamps.

- **Scanner imports trigger db.init_db()** — SQLite schema is created on first import of db module. This is intentional but means any import of scanner modules touches the filesystem.

## Architecture Notes

- **Sync vs Async scanner** — Both exist and produce identical results. Sync (`scanner.py`) is simpler to debug. Async (`async_scanner.py`) is 5x faster. Server exposes both via `/api/scan` and `/api/scan/fast`.

- **Scoring is deterministic** — Given the same z-score, half-life, and spread_std, `score_opportunity()` always returns the same grade. The brain layer is the only non-deterministic component.

- **DB schema migration** — New columns are added via `ALTER TABLE ... ADD COLUMN` in `init_db()` with try/except for idempotency. No migration framework.
