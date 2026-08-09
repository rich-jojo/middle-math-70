#!/usr/bin/env python3
"""Delete old PostgreSQL custom-format dumps after the retention window."""

from __future__ import annotations

import sys
import time
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "/srv/ssd/backups/middle-math-70")
days = int(sys.argv[2] if len(sys.argv) > 2 else "30")
cutoff = time.time() - days * 86400
removed = 0
for dump in root.glob("mm70-*.dump"):
    if dump.is_file() and dump.stat().st_mtime < cutoff:
        dump.unlink()
        removed += 1
print(f"removed={removed}")
