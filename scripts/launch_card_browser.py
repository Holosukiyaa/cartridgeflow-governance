"""Launch the local read-only governance card browser."""

from __future__ import annotations

import argparse
import importlib.util
import socket
import sys
import webbrowser
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "governance-source.sqlite"
DEFAULT_INDEX = ROOT / ".data" / "governance-index.sqlite"
DEFAULT_LEDGER = ROOT / "governance-ledger.sqlite"
DEFAULT_TARGETS = ROOT / "targets.json"


def _port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--port", type=int, default=8041)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    index = args.index.resolve()
    ledger = args.ledger.resolve()
    targets = args.targets.resolve()
    if not source.is_file() or not index.is_file() or not ledger.is_file() or not targets.is_file():
        print("Card browser requires source, index, Ledger, and target configuration files.", file=sys.stderr)
        return 1
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not _port_available(args.port):
        print(f"Card browser port is already in use: {args.port}", file=sys.stderr)
        return 1
    sys.path.insert(0, str(ROOT))
    module_path = ROOT / "viewer" / "app.py"
    specification = importlib.util.spec_from_file_location("cartridgeflow_governance_viewer_app", module_path)
    if specification is None or specification.loader is None:
        print(f"Cannot load card browser application: {module_path}", file=sys.stderr)
        return 1
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    url = f"http://127.0.0.1:{args.port}/"
    print(f"Governance card browser: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(
        module.create_app(source, index, ledger, targets),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
