import logging
from typing import Optional
from logging.handlers import RotatingFileHandler


# internal enabled flag
_enabled: bool = False


def configure_logging(to_console: bool = True, level: str = 'INFO', logfile: Optional[str] = None) -> None:
    """Configure the root logger with a console handler and optional file handler.

    - to_console: whether to attach a StreamHandler
    - level: logging level name (e.g. 'DEBUG')
    - logfile: optional file path to write logs
    """
    root_logger = logging.getLogger()
    lvl = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(lvl)

    formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')

    # Add console handler if requested and not already present
    if to_console:
        has_stream = any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers)
        if not has_stream:
            ch = logging.StreamHandler()
            ch.setLevel(lvl)
            ch.setFormatter(formatter)
            root_logger.addHandler(ch)

    # Add file handler if requested (use RotatingFileHandler to avoid oversized logs)
    if logfile:
        # Default rotation parameters
        max_bytes = 10 * 1024 * 1024  # 10 MB
        backup_count = 5
        has_file = any(isinstance(h, logging.FileHandler) and getattr(h, 'baseFilename', None) == logfile for h in root_logger.handlers)
        if not has_file:
            try:
                fh = RotatingFileHandler(logfile, maxBytes=max_bytes, backupCount=backup_count)
            except Exception:
                # fallback to basic FileHandler if RotatingFileHandler cannot be created
                fh = logging.FileHandler(logfile)
            fh.setLevel(lvl)
            fh.setFormatter(formatter)
            root_logger.addHandler(fh)


def enable_logging(to_console: bool = True, level: str = 'INFO', logfile: Optional[str] = None) -> None:
    """Enable logging globally and configure handlers.

    Calling this will un-disable the root logger and add handlers according to the
    provided arguments.
    """
    global _enabled
    configure_logging(to_console=to_console, level=level, logfile=logfile)
    logging.getLogger().disabled = False
    _enabled = True


def disable_logging() -> None:
    """Disable logging globally by setting the root logger disabled flag.

    This is a safe, simple way to silence logging without removing handlers.
    """
    global _enabled
    root = logging.getLogger()
    # Close and remove any FileHandlers to release file handles
    for h in list(root.handlers):
        if isinstance(h, logging.FileHandler):
            try:
                root.removeHandler(h)
            except Exception:
                pass
            try:
                h.close()
            except Exception:
                pass
    # Optionally keep StreamHandlers (console) but disable logging globally
    root.disabled = True
    _enabled = False


def is_enabled() -> bool:
    """Return whether logging is currently enabled via this module."""
    return _enabled
