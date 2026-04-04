"""Helper for appending structured entries to the journal log."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("scanner.journal")

JOURNAL_FILE = Path(__file__).parent / "logs" / "journal.jsonl"


def append_entry(entry: dict) -> dict:
    """Append the provided entry to the journal and return the saved payload."""
    payload = dict(entry or {})
    payload.setdefault("timestamp", datetime.now().isoformat())
    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(JOURNAL_FILE, "a") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception as exc:
        log.warning("Failed to append journal entry: %s", exc)
    return payload
