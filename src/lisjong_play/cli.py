"""lisjong-play CLI entry point。"""

import argparse
from collections.abc import Callable, Sequence

from lisjong_play.session import (
    DEFAULT_OPPONENT,
    DEFAULT_SEED,
    OPPONENT_CHOICES,
    run_cli_session,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lisjong-play",
        description="Human EAST vs selected Policy x3 を1半荘プレイします。",
    )
    parser.add_argument(
        "--opponent",
        choices=OPPONENT_CHOICES,
        default=DEFAULT_OPPONENT,
        help=f"AI opponent Policy (default: {DEFAULT_OPPONENT})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"deterministic match seed (default: {DEFAULT_SEED})",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    input_reader: Callable[[str], str] = input,
    output_writer: Callable[[str], None] = print,
) -> int:
    args = _parser().parse_args(argv)
    try:
        run_cli_session(
            seed=args.seed,
            opponent=args.opponent,
            input_reader=input_reader,
            output_writer=output_writer,
        )
    except KeyboardInterrupt:
        output_writer("対局を終了しました。")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
