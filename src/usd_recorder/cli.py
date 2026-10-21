"""Command-line entry point: ``usd-recorder flatten | inspect | replay {validate,info,convert}``."""

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

    rep = sub.add_parser(
        "replay",
        help="validate, summarise or convert a usd-replay/1 replay",
        description="Work with replays in the usd-replay/1 contract (docs/REPLAY.md).",
    )
    rsub = rep.add_subparsers(dest="replay_command", metavar="ACTION", required=True)
    val = rsub.add_parser(
        "validate",
        help="check a replay against the contract (exit 1 on problems)",
        description=(
            "Check REPLAY against every rule of usd-replay/1 and print one line per problem, "
            "with its prim path; exit 1 if there is any."
        ),
    )
    val.add_argument("replay", metavar="REPLAY", help="replay layer (.usda or .usdc)")
    inf = rsub.add_parser(
        "info",
        help="print a replay's metadata, sample ticks, sets and fields as JSON",
        description="Print REPLAY's metadata, sample ticks, entity sets, fields and stats as JSON.",
    )
    inf.add_argument("replay", metavar="REPLAY", help="replay layer (.usda or .usdc)")
    con = rsub.add_parser(
        "convert",
        help="re-export a replay as .usda or .usdc (by OUT's extension)",
        description="Validate REPLAY, then re-export it atomically to OUT; an invalid replay is not converted.",
    )
    con.add_argument("replay", metavar="REPLAY", help="replay layer to convert")
    con.add_argument("out", metavar="OUT", help="output layer; .usda writes text, .usdc binary")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "replay":
            return _replay(args)
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


def _replay(args: argparse.Namespace) -> int:
    from .replay import convert, replay_info, validate_replay

    if args.replay_command == "validate":
        problems = validate_replay(args.replay)
        if problems:
            print(f"{args.replay}: {len(problems)} problem(s)")
            for problem in problems:
                print(f"  {problem}")
            return 1
        print(f"{args.replay}: conforms to usd-replay/1")
    elif args.replay_command == "info":
        print(json.dumps(replay_info(args.replay), indent=2))
    else:
        problems = validate_replay(args.replay)
        if problems:
            raise ValueError(f"{args.replay} is not a valid replay: {problems[0]}")
        print(convert(args.replay, args.out))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

__all__ = ["build_parser", "main"]
