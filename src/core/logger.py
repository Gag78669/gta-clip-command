"""Structured logging system for GTA CLIP COMMAND with console formatting and file persistence."""

import logging
import sys
from pathlib import Path
from rich.console import Console
from rich.logging import RichHandler

_LOGGERS = {}


def setup_logger(
    name: str = "gta_clip",
    log_level: int = logging.INFO,
    log_file: Path | None = None,
) -> logging.Logger:
    """Configure a logger with Rich terminal formatting and optional disk persistence."""
    if name in _LOGGERS:
        return _LOGGERS[name]

    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    logger.propagate = False

    # Prevent duplicate handlers
    if not logger.handlers:
        # Rich Console Output
        console = Console(file=sys.stdout)
        rich_handler = RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            markup=True,
            rich_tracebacks=True,
        )
        rich_handler.setLevel(log_level)
        logger.addHandler(rich_handler)

        # File Handler (if configured)
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
            file_handler.setLevel(log_level)
            formatter = logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    _LOGGERS[name] = logger
    return logger


def get_logger(name: str = "gta_clip") -> logging.Logger:
    """Retrieve an existing logger or construct a default one."""
    if name in _LOGGERS:
        return _LOGGERS[name]
    log_dir = Path(__file__).resolve().parent.parent.parent / "data" / "logs"
    return setup_logger(name=name, log_file=log_dir / "pipeline.log")
