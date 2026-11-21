"""Offline pipeline, alerting, storage, and schema integration tests."""

import csv
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from price_drop_monitor.alerts import (
    EmailNotifier,
    WebhookNotifier,
    evaluate_alerts,
    format_consolidated_alert,
)
from price_drop_monitor.models import (
    CheckStatus,
    MonitorConfig,
    PriceCheckResult,
    ProductCategory,
    SourceType,
)
from price_drop_monitor.pipeline import run_pipeline
from price_drop_monitor.storage import GoogleSheetsAdapter, load_watchlist_csv
from validate import validate_report_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
SCHEMA_PATH = PROJECT_ROOT / "docs" / "price_report.schema.json"
CHECKED_AT = "2026-11-19T13:00:00+00:00"


def _make_result(
    *,
    product_id: str,
    name: str,
    target_price: float,
    current_price: float | None,
    old_price: float | None = None,
    status: CheckStatus = CheckStatus.OK,
    triggered_alert: bool = False,
    error_message: str | None = None,
    category: ProductCategory = ProductCategory.TECH_HARDWARE,
    source_type: SourceType = SourceType.DOM,
    url: str | None = None,
) -> PriceCheckResult:
    return PriceCheckResult(
        product_id=product_id,
        name=name,
        category=category,
        url=url or f"https://example.com/items/{product_id}",
        source_type=source_type,
        target_price=target_price,
        old_price=old_price,
        current_price=current_price,
        currency="USD",
        status=status,
        triggered_alert=triggered_alert,
        error_message=error_message,
        checked_at=CHECKED_AT,
    )


def test_evaluate_alerts_strict_threshold_and_savings() -> None:
    """Trigger only strictly below target and compute savings from old_price."""
    results = [
        _make_result(
            product_id="below-with-old",
            name="Below With Old",
            target_price=100.0,
            current_price=80.0,
            old_price=120.0,
            triggered_alert=True,
        ),
        _make_result(
            product_id="equal-to-target",
            name="Equal To Target",
            target_price=100.0,
            current_price=100.0,
        ),
        _make_result(
            product_id="above-target",
            name="Above Target",
            target_price=100.0,
            current_price=120.0,
        ),
        _make_result(
            product_id="parse-error",
            name="Parse Error",
            target_price=100.0,
            current_price=None,
            status=CheckStatus.ERROR,
            error_message="price node missing",
        ),
        _make_result(
            product_id="below-without-old",
            name="Below Without Old",
            target_price=50.0,
            current_price=40.0,
        ),
    ]

    alerts = evaluate_alerts(results)

    assert [alert.product_id for alert in alerts] == ["below-with-old", "below-without-old"]
    assert alerts[0].new_price == pytest.approx(80.0)
    assert alerts[0].savings == pytest.approx(40.0)
    assert alerts[1].old_price is None
    assert alerts[1].savings is None


def test_format_consolidated_alert_contains_required_fields() -> None:
    """List each triggered deal with price, target, savings, and URL details."""
    results = [
        _make_result(
            product_id="garmin-edge-840",
            name="Garmin Edge 840 Cycling Computer",
            category=ProductCategory.CYCLING_GEAR,
            source_type=SourceType.JSONLD,
            target_price=399.0,
            current_price=369.99,
            old_price=449.99,
            triggered_alert=True,
            url="https://example.com/cycling/garmin-edge-840",
        ),
        _make_result(
            product_id="samsung-990-pro-2tb",
            name="Samsung 990 PRO 2TB NVMe SSD",
            target_price=160.0,
            current_price=149.99,
            old_price=179.99,
            triggered_alert=True,
            url="https://example.com/hardware/samsung-990-pro-2tb",
        ),
    ]

    alerts = evaluate_alerts(results)
    message = format_consolidated_alert(alerts)

    assert "2 item(s)" in message
    for alert in alerts:
        assert alert.name in message
        assert alert.product_id in message
        assert alert.url in message
        assert f"{alert.new_price:.2f}" in message
        assert f"{alert.target_price:.2f}" in message
        assert alert.old_price is not None
        assert f"{alert.old_price:.2f}" in message
        assert alert.savings is not None
        assert f"{alert.savings:.2f}" in message


def test_email_and_webhook_notifiers_with_injected_doubles() -> None:
    """Send through fake SMTP and MockTransport without opening network sockets."""
    results = [
        _make_result(
            product_id="garmin-edge-840",
            name="Garmin Edge 840 Cycling Computer",
            category=ProductCategory.CYCLING_GEAR,
            source_type=SourceType.JSONLD,
            target_price=399.0,
            current_price=369.99,
            old_price=449.99,
            triggered_alert=True,
        )
    ]
    alerts = evaluate_alerts(results)

    class FakeSMTP:
        def __init__(self, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.started_tls = False
            self.login_args: tuple[str, str] | None = None
            self.message: Any = None
            self.quit_called = False

        def starttls(self) -> None:
            self.started_tls = True

        def login(self, user: str, password: str) -> None:
            self.login_args = (user, password)

        def send_message(self, message: Any) -> None:
            self.message = message

        def quit(self) -> None:
            self.quit_called = True

    fake_smtp = FakeSMTP("smtp.example.com", 587)
    email_notifier = EmailNotifier(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="alerts@example.com",
        smtp_app_password="app-password",
        to_email="owner@example.com",
        smtp_factory=lambda host, port: fake_smtp,
    )
    email_notifier.send(alerts)

    assert fake_smtp.started_tls is True
    assert fake_smtp.login_args == ("alerts@example.com", "app-password")
    assert fake_smtp.message is not None
    assert fake_smtp.message["Subject"] == "Price Drop Alert: 1 item(s) below target"
    assert fake_smtp.message["To"] == "owner@example.com"
    body = fake_smtp.message.get_content()
    assert "Garmin Edge 840 Cycling Computer" in body
    assert fake_smtp.quit_called is True

    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(202, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    webhook_notifier = WebhookNotifier(
        webhook_url="https://hooks.example.com/price-alert",
        client=client,
    )
    webhook_notifier.send(alerts)

    assert len(captured_requests) == 1
    payload = json.loads(captured_requests[0].content)
    assert payload["alert_count"] == 1
    assert payload["text"] == payload["content"]
    assert "Garmin Edge 840 Cycling Computer" in payload["text"]
    client.close()


def test_google_sheets_adapter_sync_with_mock_transport() -> None:
    """Read Tab 1 and append Tab 2, including fail-closed error rows."""
    header = [
        "product_id",
        "name",
        "category",
        "url",
        "target_price",
        "old_price",
        "currency",
        "source_type",
        "fixture_file",
    ]
    watchlist_row = [
        "samsung-990-pro-2tb",
        "Samsung 990 PRO 2TB PCIe 4.0 NVMe SSD",
        "tech_hardware",
        "https://example.com/hardware/samsung-990-pro-2tb",
        "160.00",
        "179.99",
        "USD",
        "dom",
        "",
    ]
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"values": [header, watchlist_row]})
        return httpx.Response(200, json={"updates": {"updatedRows": 2}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = GoogleSheetsAdapter(
        spreadsheet_id="sheet-123",
        api_token="test-token",
        client=client,
    )

    items = adapter.fetch_watchlist()
    assert len(items) == 1
    assert items[0].product_id == "samsung-990-pro-2tb"
    assert items[0].old_price == pytest.approx(179.99)
    assert items[0].fixture_file is None

    ok_result = _make_result(
        product_id="samsung-990-pro-2tb",
        name="Samsung 990 PRO 2TB PCIe 4.0 NVMe SSD",
        target_price=160.0,
        current_price=149.99,
        old_price=179.99,
        triggered_alert=True,
    )
    error_result = _make_result(
        product_id="keychron-q1-pro",
        name="Keychron Q1 Pro",
        target_price=180.0,
        current_price=None,
        old_price=199.0,
        status=CheckStatus.ERROR,
        error_message="price element missing",
    )
    adapter.append_history([ok_result, error_result])

    get_request, post_request = captured_requests
    assert get_request.method == "GET"
    assert "sheets.googleapis.com/v4/spreadsheets/sheet-123/values/" in str(get_request.url)
    assert "Watchlist!A1:I500" in str(get_request.url)
    assert get_request.headers["authorization"] == "Bearer test-token"

    assert post_request.method == "POST"
    assert "PriceHistory!A:J:append" in str(post_request.url)
    assert post_request.url.params["valueInputOption"] == "USER_ENTERED"
    payload = json.loads(post_request.content)
    assert len(payload["values"]) == 2
    error_row = payload["values"][1]
    assert error_row[4] == ""
    assert error_row[7] == "error"
    assert error_row[8] == "price element missing"
    client.close()


def test_pipeline_offline_end_to_end_and_schema_validation(tmp_path: Path) -> None:
    """Run all five fixtures offline and validate the generated report against the schema."""
    watchlist_path = tmp_path / "watchlist.csv"
    watchlist_path.write_text(
        (FIXTURES_DIR / "watchlist_targets.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = MonitorConfig(
        watchlist_path=watchlist_path,
        history_path=tmp_path / "price_history.csv",
        output_path=tmp_path / "latest_report.json",
        fixtures_dir=FIXTURES_DIR,
        offline=True,
    )

    report = run_pipeline(config)

    assert len(report.results) == 5
    statuses = [result.status for result in report.results]
    assert statuses.count(CheckStatus.OK) == 4
    assert statuses.count(CheckStatus.ERROR) == 1

    failed = next(result for result in report.results if result.product_id == "keychron-q1-pro")
    assert failed.status == CheckStatus.ERROR
    assert failed.current_price is None
    assert failed.triggered_alert is False
    assert failed.error_message
    assert failed.error_message.strip()

    assert len(report.alerts) == 4
    assert all(alert.savings is not None for alert in report.alerts)

    assert len(load_watchlist_csv(config.watchlist_path)) == 5

    with config.history_path.open(newline="", encoding="utf-8") as handle:
        history_rows = list(csv.DictReader(handle))
    assert len(history_rows) == 5
    assert b"\r" not in config.history_path.read_bytes()
    failed_row = next(row for row in history_rows if row["product_id"] == "keychron-q1-pro")
    assert failed_row["current_price"] == ""
    assert failed_row["status"] == "error"
    assert failed_row["error_message"]

    assert config.output_path.is_file()
    errors = validate_report_file(report_path=config.output_path, schema_path=SCHEMA_PATH)
    assert errors == []


def test_pipeline_fault_isolation_on_fetch_error(tmp_path: Path) -> None:
    """Keep checking healthy items after one URL fails with HTTP 503."""
    watchlist_path = tmp_path / "watchlist.csv"
    watchlist_path.write_text(
        "product_id,name,category,url,target_price,old_price,currency,source_type,fixture_file\n"
        "failing-item,Failing Item,tech_hardware,https://shop.example/fail,"
        "50.00,60.00,USD,dom,\n"
        "healthy-item,Healthy Item,tech_hardware,https://shop.example/ok,"
        "100.00,119.99,USD,dom,\n",
        encoding="utf-8",
    )
    config = MonitorConfig(
        watchlist_path=watchlist_path,
        history_path=tmp_path / "price_history.csv",
        output_path=tmp_path / "latest_report.json",
        fixtures_dir=FIXTURES_DIR,
        offline=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fail":
            return httpx.Response(503, json={"error": "temporarily unavailable"})
        return httpx.Response(
            200,
            text=(
                '<html><body><main class="product-card">'
                '<span class="price-current" data-price="89.50">$89.50</span>'
                '<span class="price-currency">USD</span>'
                "</main></body></html>"
            ),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = run_pipeline(config, http_client=client)

    assert [result.product_id for result in report.results] == ["failing-item", "healthy-item"]
    failed, healthy = report.results

    assert failed.status == CheckStatus.ERROR
    assert failed.current_price is None
    assert failed.triggered_alert is False
    assert failed.error_message
    assert "503" in failed.error_message

    assert healthy.status == CheckStatus.OK
    assert healthy.current_price == pytest.approx(89.50)
    assert healthy.triggered_alert is True
    assert healthy.error_message is None

    assert len(report.alerts) == 1
    assert report.alerts[0].product_id == "healthy-item"

    with config.history_path.open(newline="", encoding="utf-8") as handle:
        history_rows = list(csv.DictReader(handle))
    assert len(history_rows) == 2

    errors = validate_report_file(report_path=config.output_path, schema_path=SCHEMA_PATH)
    assert errors == []
    client.close()
