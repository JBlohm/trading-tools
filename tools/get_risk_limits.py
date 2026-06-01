#!/usr/bin/env python3
"""
get_risk_limits.py — Read and display configured pre-trade risk limits.

Reads risk_limits.json from the tools directory and prints it as JSON.
Supports overriding individual fields for inspection.

Usage:
    python get_risk_limits.py
    python get_risk_limits.py --key max_single_name_pct_nlv
"""

import argparse
import json
import pathlib
import sys

LIMITS_FILE = pathlib.Path(__file__).parent / "risk_limits.json"


def load_limits() -> dict:
    if not LIMITS_FILE.exists():
        raise FileNotFoundError(f"Risk limits file not found: {LIMITS_FILE}")
    data = json.loads(LIMITS_FILE.read_text())
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read configured pre-trade risk limits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python get_risk_limits.py
  python get_risk_limits.py --key max_single_name_pct_nlv
        """,
    )
    parser.add_argument(
        "--key",
        help="Return only the value for a specific limit key (from the 'limits' block)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        data = load_limits()
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc), "status": "config_missing"}), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"Invalid JSON in risk_limits.json: {exc}", "status": "config_invalid"}),
              file=sys.stderr)
        sys.exit(1)

    if args.key:
        limits = data.get("limits", {})
        if args.key not in limits:
            print(json.dumps({"error": f"Key '{args.key}' not found", "status": "not_found"}),
                  file=sys.stderr)
            sys.exit(1)
        print(json.dumps({args.key: limits[args.key]}, indent=2))
    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
