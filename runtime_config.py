"""Runtime configuration loader with macOS Keychain support.

Secrets and operator config are sourced from the macOS Keychain first-class
via the `security` CLI. Process environment variables remain supported as
high-priority overrides so tests and one-off runs can inject temporary values
without mutating the machine keychain.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("scanner.runtime_config")

KEYCHAIN_SERVICE_ENV = "SCANNER_KEYCHAIN_SERVICE"
DEFAULT_KEYCHAIN_SERVICE = "polymarket-scanner"

CONFIG_NAMES = {
    "ALCHEMY_API_KEY",
    "ANTHROPIC_API_KEY",
    "BRAIN_ANTHROPIC_COMPLEX_MODEL",
    "BRAIN_ANTHROPIC_MODEL",
    "BRAIN_OPENAI_COMPLEX_MODEL",
    "BRAIN_OPENAI_MODEL",
    "BRAIN_PROVIDER",
    "BRAIN_XAI_COMPLEX_MODEL",
    "BRAIN_XAI_MODEL",
    "EXECUTION_MODE",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "PERPLEXITY_API_KEY",
    "POLYMARKET_PRIVATE_KEY",
    "SCANNER_API_KEY",
    "SCANNER_API_KEYS",
    "SCANNER_CF_ACCESS_EMAILS",
    "SCANNER_DB_PATH",
    "STAGE2_POLYGON_GATING",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "WEATHER_EXACT_TEMP_AUTOTRADE",
    "WEATHER_EXACT_TEMP_ENABLED",
    "WEATHER_REVIEW_CONFIG_PATH",
    "XAI_API_KEY",
    "XAI_BASE_URL",
}

SECRET_NAMES = {
    "ALCHEMY_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "PERPLEXITY_API_KEY",
    "POLYMARKET_PRIVATE_KEY",
    "SCANNER_API_KEY",
    "SCANNER_API_KEYS",
    "TELEGRAM_BOT_TOKEN",
    "XAI_API_KEY",
}


def keychain_service_name() -> str:
    """Return the Keychain service namespace used for scanner config."""
    override = os.environ.get(KEYCHAIN_SERVICE_ENV)
    if override is None:
        return DEFAULT_KEYCHAIN_SERVICE
    normalized = override.strip()
    return normalized or DEFAULT_KEYCHAIN_SERVICE


def _security_cli_available() -> bool:
    return shutil.which("security") is not None


def _keychain_supported() -> bool:
    return platform.system() == "Darwin" and _security_cli_available()


@lru_cache(maxsize=None)
def _find_keychain_value(service: str, name: str) -> str | None:
    if not _keychain_supported():
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", service, "-a", name],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - depends on host OS/keychain
        log.debug("Keychain lookup failed for %s/%s: %s", service, name, exc)
        return None

    if result.returncode != 0:
        stderr = (result.stderr or "").strip().lower()
        if "could not be found" not in stderr:
            log.debug(
                "Keychain lookup returned rc=%s for %s/%s: %s",
                result.returncode,
                service,
                name,
                (result.stderr or "").strip(),
            )
        return None

    return (result.stdout or "").strip()


def clear_cache() -> None:
    """Clear cached Keychain lookups. Useful in tests."""
    _find_keychain_value.cache_clear()


def get_raw(name: str, default: str | None = None) -> str | None:
    """Return a config value from env override or Keychain, without coercion."""
    env_value = os.environ.get(name)
    if env_value is not None:
        return env_value

    value = _find_keychain_value(keychain_service_name(), name)
    if value not in (None, ""):
        return value
    return default


def get(name: str, default: str = "") -> str:
    """Return a normalized string config value."""
    value = get_raw(name, default)
    if value is None:
        return default
    return str(value).strip()


def get_bool(name: str, default: bool = False) -> bool:
    """Return a config flag parsed from env override or Keychain."""
    raw = get_raw(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def get_path(name: str, default: Path) -> Path:
    """Return a filesystem path from config or a default."""
    raw = get_raw(name)
    if raw is None or str(raw).strip() == "":
        return default
    return Path(str(raw).strip())


def runtime_status() -> dict:
    """Return a redacted view of config/keychain availability for logging."""
    service = keychain_service_name()
    env_overrides = []
    keychain_entries = []
    for name in sorted(CONFIG_NAMES):
        if os.environ.get(name) is not None:
            env_overrides.append(name)
            continue
        if _find_keychain_value(service, name):
            keychain_entries.append(name)

    return {
        "service": service,
        "keychain_supported": _keychain_supported(),
        "security_cli_available": _security_cli_available(),
        "env_overrides": env_overrides,
        "keychain_entries": keychain_entries,
        "live_ready": bool(get("POLYMARKET_PRIVATE_KEY") and get("ALCHEMY_API_KEY")),
    }


def log_runtime_status(context: str) -> dict:
    """Emit a startup/runtime audit line without exposing secret values."""
    status = runtime_status()
    log.info(
        "Runtime config (%s): service=%s keychain_supported=%s env_overrides=%s keychain_entries=%s live_ready=%s",
        context,
        status["service"],
        status["keychain_supported"],
        ",".join(status["env_overrides"]) or "none",
        ",".join(status["keychain_entries"]) or "none",
        status["live_ready"],
    )
    if not status["keychain_supported"]:
        log.warning(
            "Runtime config (%s): macOS Keychain access unavailable; only process env overrides will be used",
            context,
        )
    return status
