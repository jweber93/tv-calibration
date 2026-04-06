"""Runtime dependency checks and console helpers."""

from __future__ import annotations

import importlib

from rich.console import Console

REQUIRED_PACKAGES = ["rich", "numpy"]


def ensure_packages() -> None:
    """Raise ImportError if any required package is not installed."""
    missing = []
    for pkg in REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        raise ImportError(
            f"Missing required packages: {', '.join(missing)}. "
            "Run: pip install -r requirements.txt"
        )


console = Console()
