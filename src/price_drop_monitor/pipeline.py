"""Fault-isolated daily price check pipeline."""

import json
import os
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .alerts import EmailNotifier, WebhookNotifier, evaluate_alerts
from .extractors import extract_price
from .models import (
    CheckStatus,
    MonitorConfig,
    PriceAlert,
    PriceCheckResult,
    PriceReport,
    WatchlistItem,
)
from .storage import GoogleSheetsAdapter, append_history_csv, load_watchlist_csv


def _utc_timestamp(now: datetime | None) -> str:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.isoformat(timespec="seconds")


def _error_message(exc: Exception) -> str:
    message = str(exc).strip()
    return message or f"{exc.__class__.__name__} during price check"


def _load_watchlist(
    config: MonitorConfig,
    sheets_adapter: GoogleSheetsAdapter | None,
) -> list[WatchlistItem]:
    if not config.offline and sheets_adapter is not None:
        return sheets_adapter.fetch_watchlist()

    watchlist_path = config.watchlist_path
    if config.offline and not watchlist_path.is_file():
        fallback = config.fixtures_dir / "watchlist_targets.csv"
        watchlist_path.parent.mkdir(parents=True, exist_ok=True)
        watchlist_path.write_text(fallback.read_text(encoding="utf-8"), encoding="utf-8")
    return load_watchlist_csv(watchlist_path)


def _fetch_content(
    item: WatchlistItem,
    config: MonitorConfig,
    http_client: httpx.Client | None,
) -> str:
    if config.offline:
        if not item.fixture_file:
            raise ValueError(f"Offline mode requires fixture_file for {item.product_id}")
        return (config.fixtures_dir / item.fixture_file).read_text(encoding="utf-8")
    if http_client is None:
        raise RuntimeError("An HTTP client is required when offline=False")
    response = http_client.get(item.url)
    response.raise_for_status()
    return response.text


def _check_item(
    item: WatchlistItem,
    config: MonitorConfig,
    http_client: httpx.Client | None,
    checked_at: str,
) -> PriceCheckResult:
    try:
        content = _fetch_content(item, config, http_client)
        extracted = extract_price(content, item.source_type, item.currency)
        if extracted.status == CheckStatus.OK and extracted.price is None:
            raise ValueError("Extractor reported ok without a current price")
        triggered_alert = (
            extracted.status == CheckStatus.OK
            and extracted.price is not None
            and extracted.price < item.target_price
        )
        return PriceCheckResult(
            product_id=item.product_id,
            name=item.name,
            category=item.category,
            url=item.url,
            source_type=item.source_type,
            target_price=item.target_price,
            old_price=item.old_price,
            current_price=extracted.price,
            currency=extracted.currency,
            status=extracted.status,
            triggered_alert=triggered_alert,
            error_message=extracted.error_message,
            checked_at=checked_at,
        )
    except Exception as exc:  # noqa: BLE001 - one bad item must not abort the batch
        return PriceCheckResult(
            product_id=item.product_id,
            name=item.name,
            category=item.category,
            url=item.url,
            source_type=item.source_type,
            target_price=item.target_price,
            old_price=item.old_price,
            current_price=None,
            currency=item.currency,
            status=CheckStatus.ERROR,
            triggered_alert=False,
            error_message=_error_message(exc),
            checked_at=checked_at,
        )


def _write_report_atomic(path: Path, report: PriceReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _dispatch_alerts(
    config: MonitorConfig,
    alerts: Sequence[PriceAlert],
    email_notifier: EmailNotifier | None,
    webhook_notifier: WebhookNotifier | None,
) -> None:
    if not config.send_alerts or not alerts:
        return

    email = email_notifier
    if email is None and config.smtp_host and config.alert_to_email:
        email = EmailNotifier(
            smtp_host=config.smtp_host,
            smtp_port=config.smtp_port,
            smtp_user=config.smtp_user,
            smtp_app_password=config.smtp_app_password,
            to_email=config.alert_to_email,
        )
    if email is not None:
        email.send(alerts)

    webhook = webhook_notifier
    if webhook is None and config.webhook_alert_url:
        webhook = WebhookNotifier(config.webhook_alert_url)
    if webhook is not None:
        webhook.send(alerts)


def run_pipeline(
    config: MonitorConfig,
    *,
    http_client: httpx.Client | None = None,
    sheets_adapter: GoogleSheetsAdapter | None = None,
    email_notifier: EmailNotifier | None = None,
    webhook_notifier: WebhookNotifier | None = None,
    now: datetime | None = None,
) -> PriceReport:
    """Run one fault-isolated check cycle and persist history plus the JSON snapshot."""
    client = http_client
    owns_client = False
    if client is None and not config.offline:
        client = httpx.Client(timeout=30.0, follow_redirects=True)
        owns_client = True

    adapter = sheets_adapter
    owns_adapter = False
    if (
        adapter is None
        and not config.offline
        and config.google_sheets_spreadsheet_id
        and config.google_sheets_api_token
    ):
        adapter = GoogleSheetsAdapter(
            config.google_sheets_spreadsheet_id,
            config.google_sheets_api_token,
            client=client,
        )
        owns_adapter = True

    try:
        checked_at = _utc_timestamp(now)
        watchlist = _load_watchlist(config, adapter)
        results = [_check_item(item, config, client, checked_at) for item in watchlist]
        alerts = evaluate_alerts(results)
        report = PriceReport(generated_at=checked_at, results=results, alerts=alerts)

        append_history_csv(config.history_path, results)
        if not config.offline and adapter is not None:
            adapter.append_history(results)
        _write_report_atomic(config.output_path, report)
        _dispatch_alerts(config, alerts, email_notifier, webhook_notifier)
        return report
    finally:
        if owns_adapter and adapter is not None:
            adapter.close()
        if owns_client and client is not None:
            client.close()
