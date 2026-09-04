from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

from tui.wakeup import run_wakeup

VERSION = "0.1.0"


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="synapse",
        description="Synapse - AI-powered coding agent CLI",
    )
    parser.add_argument("-v", "--version", action="version", version=VERSION)

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("wakeup", help="Show the Synapse wakeup screen")

    args = parser.parse_args()

    if args.command == "wakeup":
        asyncio.run(run_wakeup())
        return

    parser.print_help()


if __name__ == "__main__":
    main()
