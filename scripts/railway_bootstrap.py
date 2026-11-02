"""Backward-compatible entry point. Database bootstrap now targets PostgreSQL."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.db_bootstrap import bootstrap_with_app, main  # noqa: E402, F401

__all__ = ["bootstrap_with_app", "main"]

if __name__ == "__main__":
    main()
