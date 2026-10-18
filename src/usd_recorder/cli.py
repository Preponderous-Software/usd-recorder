"""Command-line entry point: ``usd-recorder flatten`` and ``usd-recorder inspect``."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .clips import flatten
from .inspect import format_summary, inspect_stage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="usd-recorder",
        description="Inspect and flatten time-sampled OpenUSD recordings.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    flat = sub.add_parser(
        "flatten",
        help="bake a (chunked) recording into one layer",
        description=(
            "Resolve every value clip of RUN and write one layer with all time samples baked in: "
            "<stem>_flat.usda next to RUN unless -o is given (.usdc with --binary)."
        ),
    )
    flat.add_argument("run", metavar="RUN", help="root layer of the recording")
    flat.add_argument("-o", "--out", default=None, metavar="FILE", help="output layer (.usda or .usdc)")
    flat.add_argument("--binary", action="store_true", help="write binary .usdc")

    ins = sub.add_parser("inspect", help="summarise a recording", description="Summarise a recorded stage.")
    ins.add_argument("run", metavar="RUN", help="root layer of the recording")
    ins.add_argument("--json", action="store_true", help="print the summary as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "flatten":
            out = flatten(args.run, args.out, binary=args.binary)
            print(out)
        else:
            summary = inspect_stage(args.run)
            print(json.dumps(summary, indent=2) if args.json else format_summary(summary))
    except (OSError, ValueError) as exc:
        print(f"usd-recorder: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

__all__ = ["build_parser", "main"]
