"""Strict Draft 2020-12 JSON Schema validation for the latest report snapshot."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import jsonschema

DEFAULT_REPORT_PATH = Path("data/latest_report.json")
DEFAULT_SCHEMA_PATH = Path("docs/price_report.schema.json")


def validate_report_file(
    report_path: Path = DEFAULT_REPORT_PATH,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
) -> list[str]:
    """Return human-readable validation errors; an empty list means the report is valid.

    File and JSON decoding errors propagate to the caller so the CLI can exit non-zero.
    """
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )
    errors = sorted(validator.iter_errors(report), key=lambda error: [str(p) for p in error.path])
    formatted: list[str] = []
    for error in errors:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        formatted.append(f"{location}: {error.message}")
    return formatted


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the report file and return POSIX exit code 0 or 1."""
    parser = argparse.ArgumentParser(
        description="Validate data/latest_report.json against docs/price_report.schema.json.",
    )
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help="Report JSON path.")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH), help="Schema JSON path.")
    args = parser.parse_args(argv)

    try:
        errors = validate_report_file(
            report_path=Path(args.report),
            schema_path=Path(args.schema),
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1

    if errors:
        print(f"Validation failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"OK: {args.report} validates against {args.schema}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
