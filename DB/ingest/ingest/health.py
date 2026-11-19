"""Docker HEALTHCHECK: healthy if the worker completed a poll recently.

The worker touches the heartbeat file after every receive (at most every ~20 s with long
polling). If it is stuck or the database has been down for a while, the file gets old.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

MAX_AGE_S = 120


def main() -> int:
    path = Path(os.environ.get("HEALTH_FILE", "/tmp/ingest-heartbeat"))
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        print("no heartbeat yet")
        return 1
    if age > MAX_AGE_S:
        print(f"last heartbeat {age:.0f}s ago")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
