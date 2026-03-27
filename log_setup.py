"""Logging setup — every decision timestamped to console + file."""
import logging
from logging.handlers import RotatingFileHandler
import sys
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "scanner.log"


def init_logging(level=logging.INFO):
    """Configure root 'scanner' logger with console + file handlers."""
    LOG_DIR.mkdir(exist_ok=True)

    logger = logging.getLogger("scanner")
    logger.setLevel(level)

    # Skip if already configured (re-import guard)
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console — INFO+
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # File — DEBUG+ (captures everything), rotating: 5MB per file, 5 backups
    fh = RotatingFileHandler(
        str(LOG_FILE), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger
