# Daily-report consolidation (2026-04-03)

- Cleaned the daily-report generator so it always prunes any `*-daily-report*` siblings and keeps one canonical `reports/2026-04-03-daily-report.md` per date, eliminating the legacy needed/create split files.
- Updated the dashboard copy to spell out that the UI now relies on a single saved Markdown file per day and no longer surfaces redundant needed/create actions, and added a reminder near the queue about using `fix_logs/` or `reports/diagnostics/` for follow-up notes.
- Marked the 2026-04-03 daily report item as resolved and documented the workflow change in the repo instructions (CLAUDE/GEMINI/AGENTS) so future agents know to keep everything consolidated.
