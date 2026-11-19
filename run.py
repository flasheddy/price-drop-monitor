"""CLI entry point for one price-drop monitoring run."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

# Keep the standalone CLI runnable with ``python run.py`` before the package is installed.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from price_drop_monitor.models import MonitorConfig
from price_drop_monitor.pipeline import run_pipeline


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _smtp_port() -> int:
    raw_port = _env("SMTP_PORT")
    if raw_port is None:
        return 587
    try:
        return int(raw_port)
    except ValueError:
        return 587


def _build_config(args: argparse.Namespace) -> MonitorConfig:
    return MonitorConfig(
        watchlist_path=Path(args.watchlist),
        history_path=Path(args.history),
        output_path=Path(args.output),
        offline=bool(args.offline),
        send_alerts=bool(args.send_alerts),
        google_sheets_spreadsheet_id=_env("GOOGLE_SHEETS_SPREADSHEET_ID"),
        google_sheets_api_token=_env("GOOGLE_SHEETS_API_TOKEN"),
        smtp_host=_env("SMTP_HOST"),
        smtp_port=_smtp_port(),
        smtp_user=_env("SMTP_USER"),
        smtp_app_password=_env("SMTP_APP_PASSWORD"),
        alert_to_email=_env("ALERT_TO_EMAIL"),
        webhook_alert_url=_env("WEBHOOK_ALERT_URL"),
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one price-drop monitor cycle and write a schema-valid report.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Read sanitized fixtures from tests/fixtures instead of live URLs.",
    )
    parser.add_argument("--watchlist", default="data/watchlist.csv", help="Watchlist CSV path.")
    parser.add_argument("--history", default="data/price_history.csv", help="History CSV path.")
    parser.add_argument("--output", default="data/latest_report.json", help="JSON report path.")
    parser.add_argument(
        "--send-alerts",
        action="store_true",
        help="Dispatch configured email/webhook alerts for triggered deals.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the pipeline once and print a short summary."""
    args = _parse_args(argv)
    config = _build_config(args)
    report = run_pipeline(config)

    ok_count = sum(1 for result in report.results if result.status == "ok")
    error_count = len(report.results) - ok_count
    print(
        f"Checked {len(report.results)} item(s): "
        f"{ok_count} ok, {error_count} error, {len(report.alerts)} alert(s)."
    )
    print(f"Report written to {config.output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
