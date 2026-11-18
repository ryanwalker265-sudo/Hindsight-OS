"""Run hindsightd.

On the device (installed by the image build):
    python3 -m hindsightd

On your own computer, for trying the interface in a normal browser:
    python3 -m hindsightd --dev        then open http://127.0.0.1:7070
"""
from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from . import __version__
from .server import Hindsight, serve

REPO = Path(__file__).resolve().parent.parent
DEVICE_SHARE = Path("/usr/share/hindsight")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hindsightd", description="Hindsight system service")
    ap.add_argument("--dev", action="store_true",
                    help="use this repository's folders and keep data in ./.hindsight-data")
    ap.add_argument("--data", help="where app data and snapshots live")
    ap.add_argument("--share", help="folder containing shell/, sdk/ and apps/")
    ap.add_argument("--apps", action="append", default=[], help="extra app folder (repeatable)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7070)
    ap.add_argument("--backend", choices=["auto", "btrfs", "copy"], default="auto")
    ap.add_argument("--interval", type=int, default=60,
                    help="default seconds between automatic Rewind points")
    ap.add_argument("--version", action="version", version=f"hindsightd {__version__}")
    args = ap.parse_args(argv)

    if args.dev:
        share = Path(args.share) if args.share else REPO
        data = Path(args.data) if args.data else REPO / ".hindsight-data"
    else:
        share = Path(args.share) if args.share else DEVICE_SHARE
        data = Path(args.data) if args.data else Path("/var/lib/hindsight")

    hs = Hindsight(
        data_root=data,
        app_dirs=[share / "apps", *args.apps],
        shell_dir=share / "shell",
        sdk_dir=share / "sdk",
        backend=args.backend,
        default_interval=args.interval,
    )
    httpd = serve(hs, args.host, args.port)
    hs.start_scheduler()
    print(f"hindsightd {__version__}: http://{args.host}:{args.port}  "
          f"data={data}  rewind={hs.backend.name}", flush=True)

    def stop(*_):
        hs.scheduler.stop()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
