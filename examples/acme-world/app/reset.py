"""Deterministic reset: restore the logical initial state from a fixture.

Usage: python -m app.reset /fixtures/acme.json
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.store import reset


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m app.reset <fixture.json>", file=sys.stderr)
        return 2
    try:
        document = reset(Path(args[0]))
    except (OSError, ValueError) as exc:
        print(f"reset failed: {exc}", file=sys.stderr)
        return 1
    counts = {
        key: len(value) for key, value in sorted(document.items()) if isinstance(value, dict | list)
    }
    print(f"reset complete: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
