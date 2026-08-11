"""Run the card browser Playwright checks with a managed local server."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]


def _wait_ready(url: str, process: subprocess.Popen[bytes]) -> None:
    for _ in range(80):
        if process.poll() is not None:
            raise RuntimeError("card browser stopped before becoming ready")
        try:
            with urlopen(url + "/health", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.25)
    raise RuntimeError("card browser did not become ready within 20 seconds")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8041)
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts" / "launch_card_browser.py"),
            "--no-browser",
            "--port",
            str(args.port),
        ],
        cwd=ROOT,
    )
    try:
        _wait_ready(url, process)
        environment = os.environ.copy()
        environment["CARD_BROWSER_URL"] = url
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tests" / "playwright_card_browser.py")],
            cwd=ROOT,
            env=environment,
            check=False,
        )
        return completed.returncode
    except (OSError, RuntimeError) as exc:
        print(f"Card browser end-to-end test failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
