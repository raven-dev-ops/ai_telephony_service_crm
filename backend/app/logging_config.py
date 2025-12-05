from __future__ import annotations

import logging
import sys


def configure_logging() -> None:
    """Configure basic structured logging for the backend.

    This keeps things simple (stdout, single formatter) while including
    useful fields like level and logger name. In a real deployment,
    logs would typically be shipped to a central system.
    """
    root = logging.getLogger()
    if root.handlers:
        # Assume logging already configured.
        return

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(logging.INFO)

