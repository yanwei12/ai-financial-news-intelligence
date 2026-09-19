"""
Cost and latency of saved analyses. Read-only.

    python -m scripts.usage_report
    python -m scripts.usage_report --input-price 0.30 --output-price 2.50

Prices are per million tokens and are yours to supply; none are built in.
"""

import argparse
import sys

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from src.ai.usage import format_summary, summarize
from src.api.headline import daily_limit
from src.database.database import engine


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-price", type=float, help="price per million input tokens")
    parser.add_argument("--output-price", type=float, help="price per million output tokens (thinking tokens included)")
    args = parser.parse_args(argv)
    if (args.input_price is None) != (args.output_price is None):
        parser.error("give both --input-price and --output-price, or neither")

    try:
        with Session(engine) as session:
            summary = summarize(session)
    except OperationalError:
        # A fresh database has no analysis table until the app has started once.
        print("No analyses saved yet (the table does not exist). Start the app and analyze an article first.")
        return 0
    print(format_summary(summary, daily_limit(), args.input_price, args.output_price))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
