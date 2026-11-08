"""Export the full business state as one JSON document on stdout.

Usage: python -m app.export_state
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from app.store import load


def main() -> int:
    document = load()
    metadata = document.setdefault("metadata", {})
    metadata["generated_at"] = datetime.now(UTC).isoformat()
    sys.stdout.write(json.dumps(document, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
