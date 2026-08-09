#!/usr/bin/env python3
"""Run an account-less Cloudflare tunnel and persist its current public URL."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

URL_RE = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com")
ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
LOG = RUNTIME / "cloudflared.log"
URL_FILE = RUNTIME / "public-url.txt"


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    cloudflared = (
        os.environ.get("CLOUDFLARED") or shutil.which("cloudflared") or str(Path.home() / "bin/cloudflared")
    )
    command = [cloudflared, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8000"]
    with LOG.open("w", encoding="utf-8") as log:
        # The executable comes from the operator-controlled service environment;
        # arguments are a fixed list and never pass through a shell.
        process = subprocess.Popen(  # noqa: S603
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
            log.flush()
            sys.stdout.write(line)
            sys.stdout.flush()
            match = URL_RE.search(line)
            if match:
                atomic_write(URL_FILE, match.group(0) + "\n")
        return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
