"""Command line:  python -m data_transformer SOURCE --config configs/ [--format xml] [--schema dental]
                                                   [--languages fr]"""
import argparse
import json
import sys
from datetime import date

from .errors import ConfigError
from .pipeline import transform


def main(argv=None) -> int:
    """Parse arguments, run transform(), print the report as JSON. Exit code 0 = valid, 1 = invalid,
    2 = configuration error."""
    parser = argparse.ArgumentParser(prog="data_transformer")
    parser.add_argument("source", help="path to a .json/.xml/.csv file")
    parser.add_argument("--config", "-c", action="append", required=True,
                        help="config file or directory (repeatable)")
    parser.add_argument("--format", choices=["json", "xml", "csv"], help="override format detection")
    parser.add_argument("--schema", help="force one schema by name (skip similarity selection)")
    parser.add_argument("--languages", help="comma-separated languages to accept, e.g. 'fr' or 'en,fr' "
                                            "(default: every language the schema enables)")
    parser.add_argument("--csv-delimiter", help="CSV delimiter (default: auto-detect)")
    parser.add_argument("--today", help="reference date YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)
    try:
        report = transform(args.source, args.config, source_format=args.format, schema=args.schema,
                           csv_delimiter=args.csv_delimiter,
                           languages=args.languages.split(",") if args.languages else None,
                           today=date.fromisoformat(args.today) if args.today else None)
    except ConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), indent=2, default=str, ensure_ascii=False))
    return 0 if report.is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
