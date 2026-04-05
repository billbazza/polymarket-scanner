"""Weather-token risk-review metadata for controlled reopen/noise experiments."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import runtime_config

log = logging.getLogger("scanner.weather_review")

_CONFIG_ENV = "WEATHER_REVIEW_CONFIG_PATH"
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "reports" / "diagnostics" / "weather-token-reopen-approved.json"


def _config_path() -> Path:
    env_path = runtime_config.get_raw(_CONFIG_ENV)
    if env_path:
        return Path(env_path)
    return _DEFAULT_CONFIG_PATH


@lru_cache(None)
def _load_config() -> dict[str, Any]:
    default = {"approved_markets": [], "default_allow_modes": ["paper", "penny"]}
    path = _config_path()
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text())
    except Exception as exc:  # pragma: no cover - best effort config load
        log.warning("Failed to read weather review config %s: %s", path, exc)
        return default
    if not isinstance(data, dict):
        log.warning("Weather review config %s is not a JSON object, ignoring", path)
        return default
    approved = data.get("approved_markets")
    if not isinstance(approved, list):
        approved = []
    default_modes = data.get("default_allow_modes")
    if not isinstance(default_modes, list):
        default_modes = default["default_allow_modes"]
    return {
        "approved_markets": approved,
        "default_allow_modes": [str(mode).strip().lower() for mode in default_modes if mode],
    }


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _contains(source: str, criterion: Any) -> bool:
    if not criterion:
        return True
    source = source.lower()
    if isinstance(criterion, (list, tuple)):
        return all(str(item).lower() in source for item in criterion if item)
    return str(criterion).lower() in source


def _exact_match(source: str, criterion: Any) -> bool:
    if not criterion:
        return True
    return source == str(criterion).strip().lower()


def _normalize_modes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        entries = value
    else:
        entries = [value]
    normalized = []
    for item in entries:
        if item:
            normalized.append(str(item).strip().lower())
    return list(dict.fromkeys(normalized))


def _mode_allowed(entry: dict[str, Any], mode: str | None, default_modes: list[str]) -> bool:
    allow_modes = entry.get("allow_modes")
    if allow_modes is None:
        allow_modes = default_modes
    normalized = {str(item).strip().lower() for item in allow_modes if item}
    if not normalized:
        return mode is None
    if "any" in normalized:
        return True
    if not mode:
        return False
    return mode.lower() in normalized


def _matches_entry(entry: dict[str, Any], signal: dict[str, Any]) -> bool:
    market = _normalize_text(signal.get("market"))
    event = _normalize_text(signal.get("event"))
    market_id = _normalize_text(signal.get("market_id"))
    target_date = _normalize_text(signal.get("target_date"))
    city = _normalize_text(signal.get("city"))

    if entry.get("market_id"):
        if _normalize_text(entry["market_id"]) != market_id:
            return False
    if entry.get("market_exact") and not _exact_match(market, entry["market_exact"]):
        return False
    if entry.get("event_exact") and not _exact_match(event, entry["event_exact"]):
        return False
    if entry.get("target_date") and _normalize_text(entry["target_date"]) != target_date:
        return False
    if entry.get("city") and entry["city"].strip().lower() != city:
        return False
    if not _contains(market, entry.get("market_contains")):
        return False
    if not _contains(event, entry.get("event_contains")):
        return False
    return True


def get_review_for_signal(signal: dict[str, Any], mode: str | None = None) -> dict[str, Any] | None:
    config = _load_config()
    default_modes = config.get("default_allow_modes", [])
    for entry in config.get("approved_markets", []):
        if not _mode_allowed(entry, mode, default_modes):
            continue
        if not _matches_entry(entry, signal):
            continue
        allow_modes = _normalize_modes(entry.get("allow_modes", default_modes))
        return {
            "id": entry.get("id") or entry.get("name"),
            "name": entry.get("name"),
            "notes": entry.get("notes"),
            "relax_noise_guard": bool(entry.get("relax_noise_guard")),
            "relax_token_guard": bool(entry.get("relax_token_guard", True)),
            "allow_modes": allow_modes,
            "probation_limit": entry.get("probation_limit"),
            "mode": mode,
        }
    return None


def reload_config() -> dict[str, Any]:
    _load_config.cache_clear()
    return _load_config()
