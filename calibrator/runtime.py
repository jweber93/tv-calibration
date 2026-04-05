"""Runtime dependency checks and console helpers."""

from __future__ import annotations

import importlib

from rich.console import Console

REQUIRED_PACKAGES = ["rich", "numpy"]


def ensure_packages() -> None:
    missing = []
    for pkg in REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        missing_list = ", ".join(missing)
        raise ImportError(
            f"Missing required packages: {missing_list}. "
            "Install dependencies with `pip install -r requirements.txt`."
        )


console = Console()
