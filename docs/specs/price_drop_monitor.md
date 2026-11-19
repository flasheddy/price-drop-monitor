# Engineering Specification — Price Drop Monitor

- **Status:** APPROVED
- **Owner:** Price Drop Monitor Engineering
- **Target Package(s):** `src/price_drop_monitor`
- **Inputs:** `docs/project_brief.md`, `docs/price_report.schema.json`, `tests/fixtures/`
- **Core Invariant:** Zero internal engine leakage; 100% public PyPI; offline deterministic tests; fail-closed price parsing (`status="error"` instead of guessing).

---

## 1. Objective & Scope

Deliver a standalone, zero-hosting-cost Python 3.11+ price-monitoring, history-logging, and threshold-alerting pipeline (`price-drop-monitor`) that checks tracked products once daily (`0 13 * * *` UTC / 08:00 AM US Eastern via GitHub Actions or on-demand CLI), appends timestamped check results to persistent history storage, validates the latest run snapshot against a strict Draft 2020-12 JSON Schema, and dispatches a single consolidated alert when one or more items drop strictly below their configured `target_price`.

### In-Scope Capabilities

1. **Watchlist Sources (Tab 1):**
   - Local CSV reader (`data/watchlist.csv`, with offline fallback to `tests/fixtures/watchlist_targets.csv`).
   - Optional Google Sheets Tab 1 (`Watchlist`) REST reader activated when `GOOGLE_SHEETS_SPREADSHEET_ID` and `GOOGLE_SHEETS_API_TOKEN` are configured.
2. **Product Categories (`ProductCategory`):**
   - `cycling_gear` — GPS cycling computers, performance tires, drivetrains, sensors, accessories.
   - `tech_hardware` — NVMe SSDs, mechanical keyboards, monitors, components, peripherals.
   - `software_deal` — IDE suites, developer tools, annual SaaS subscriptions.
3. **Pure Extraction Strategies (`SourceType`):**
   - `jsonld`: Parse `script[type="application/ld+json"]` blocks for `schema.org/Product` offer metadata (`offers.price`, `offers.priceCurrency`, `offers.availability`), supporting top-level `Product` objects, root JSON arrays, `@graph` containers, and `offers` formatted as either a single object or a list of offer objects.
   - `dom`: Parse HTML via `selectolax.lexbor.LexborHTMLParser` using `.product-card .price-current[data-price]` attribute extraction with text-content numeric parsing and `.price-currency` / symbol currency fallback.
   - `json_api`: Parse JSON API endpoint payloads (`price`, `currency`, `in_stock`).
4. **History Persistence (Tab 2):**
   - Local CSV appender (`data/price_history.csv`) recording `checked_at,product_id,name,category,current_price,target_price,currency,status,error_message,url`.
   - Optional Google Sheets Tab 2 (`PriceHistory`) row appender via Google Sheets REST API v4.
5. **Consolidated Alerting:**
   - Strict threshold evaluation (`status == "ok" and current_price is not None and current_price < target_price`).
   - Single consolidated summary report dispatched via injectable `EmailNotifier` (`smtplib` STARTTLS) and/or `WebhookNotifier` (`httpx` HTTP POST for ntfy, Discord, or custom webhooks).
6. **Schema Gate & Automation:**
   - Write `data/latest_report.json` and validate 100% against `docs/price_report.schema.json` using `validate.py` (`jsonschema.Draft202012Validator`).
   - Scheduled GitHub Actions workflow (`.github/workflows/price_monitor.yml`) and pinned `requirements.txt`.

---

## 2. Extraction Targets & Sanitized Fixture Contract

All unit and integration tests execute 100% offline against sanitized static fixtures in `tests/fixtures/`.

| Fixture File | `product_id` | `category` | `source_type` | `target_price` | `old_price` | Expected `current_price` | Expected `status` | Expected `triggered_alert` | Expected `savings` | Contract Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `watchlist_targets.csv` | (5 items below) | mixed | mixed | mixed | mixed | N/A | N/A | N/A | N/A | Canonical 5-item offline watchlist table |
| `cycling_garmin_edge.html` | `garmin-edge-840` | `cycling_gear` | `jsonld` | `399.00` | `449.99` | `369.99` | `"ok"` | `True` | `80.00` | Top-level `@type: "Product"` with dict `offers` |
| `cycling_gp5000_tire.html` | `conti-gp5000-str` | `cycling_gear` | `jsonld` | `70.00` | `79.95` | `62.50` | `"ok"` | `True` | `17.45` | `@graph` container with list `offers` |
| `hardware_nvme_ssd.html` | `samsung-990-pro-2tb` | `tech_hardware` | `dom` | `160.00` | `179.99` | `149.99` | `"ok"` | `True` | `30.00` | `.product-card .price-current[data-price="149.99"]` + `.price-currency` |
| `hardware_missing_price.html` | `keychron-q1-pro` | `tech_hardware` | `dom` | `180.00` | `199.00` | `None` | `"error"` | `False` | `None` | Missing `.price-current`; fails closed with non-empty `error_message` |
| `software_deal_api.json` | `jetbrains-toolbox-deal` | `software_deal` | `json_api` | `220.00` | `289.00` | `199.00` | `"ok"` | `True` | `90.00` | JSON payload `{"price": 199.00, "currency": "USD", "in_stock": true}` |

### Fail-Closed Fixture Behavior (`hardware_missing_price.html`)

When parsing `hardware_missing_price.html` (or any malformed HTML/JSON payload where a finite numeric price `>= 0` cannot be extracted):

- `status` MUST be `"error"`.
- `current_price` MUST be `None` (`null` in JSON, empty cell in CSV/Sheets alongside `status="error"`).
- `triggered_alert` MUST be `False`.
- `error_message` MUST be a non-empty descriptive string explaining the extraction failure.
- The pipeline MUST NOT guess `0`, `0.0`, `old_price`, or `target_price`.

---

## 3. Data Contracts & Schema Mapping

All domain models reside in `src/price_drop_monitor/models.py` using Pydantic V2 (`BaseModel`, `ConfigDict`, `Field`, `model_validator`) and standard library `StrEnum`.

```python
from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProductCategory(StrEnum):
    CYCLING_GEAR = "cycling_gear"
    TECH_HARDWARE = "tech_hardware"
    SOFTWARE_DEAL = "software_deal"


class SourceType(StrEnum):
    JSONLD = "jsonld"
    DOM = "dom"
    JSON_API = "json_api"


class CheckStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class WatchlistItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    category: ProductCategory
    url: str = Field(pattern=r"^https?://")
    target_price: float = Field(gt=0)
    old_price: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    source_type: SourceType
    fixture_file: str | None = Field(default=None)


class ExtractedPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    status: CheckStatus
    error_message: str | None = None


class PriceCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    category: ProductCategory
    url: str = Field(pattern=r"^https?://")
    source_type: SourceType
    target_price: float = Field(gt=0)
    old_price: float | None = Field(default=None, ge=0)
    current_price: float | None = Field(default=None, ge=0)
    currency: str = Field(min_length=3, max_length=3)
    status: CheckStatus
    triggered_alert: bool
    error_message: str | None = None
    checked_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def _enforce_fail_closed_invariants(self) -> "PriceCheckResult":
        if self.status == CheckStatus.ERROR:
            if self.current_price is not None:
                raise ValueError("current_price must be None when status is 'error'")
            if self.triggered_alert:
                raise ValueError("triggered_alert must be False when status is 'error'")
            if not self.error_message or not self.error_message.strip():
                raise ValueError("error_message must be non-empty when status is 'error'")
        else:
            if self.current_price is None:
                raise ValueError("current_price must be non-null when status is 'ok'")
            if self.error_message is not None:
                raise ValueError("error_message must be None when status is 'ok'")
        return self


class PriceAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    category: ProductCategory
    url: str = Field(pattern=r"^https?://")
    old_price: float | None = Field(default=None, ge=0)
    new_price: float = Field(ge=0)
    target_price: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    savings: float | None = Field(default=None, ge=0)


class PriceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: str = Field(min_length=1)
    results: list[PriceCheckResult]
    alerts: list[PriceAlert]


class MonitorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watchlist_path: Path = Path("data/watchlist.csv")
    history_path: Path = Path("data/price_history.csv")
    output_path: Path = Path("data/latest_report.json")
    fixtures_dir: Path = Path("tests/fixtures")
    offline: bool = False
    send_alerts: bool = False
    google_sheets_spreadsheet_id: str | None = None
    google_sheets_api_token: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_app_password: str | None = None
    alert_to_email: str | None = None
    webhook_alert_url: str | None = None
```

### Schema Alignment with `docs/price_report.schema.json`

- `PriceReport.model_dump(mode="json")` produces an exact match for the root schema (`generated_at`, `results`, `alerts`, `"additionalProperties": false`).
- `PriceCheckResult.model_dump(mode="json")` matches `#/$defs/result` across all 13 required keys and value constraints.
- `PriceAlert.model_dump(mode="json")` matches `#/$defs/alert` across all 9 required keys and value constraints.

---

## 4. Module Layout & Diff Boundaries

| Module Path | Action | Responsibility |
| :--- | :--- | :--- |
| `src/price_drop_monitor/models.py` | Create | Pydantic V2 schemas (`WatchlistItem`, `ExtractedPrice`, `PriceCheckResult`, `PriceAlert`, `PriceReport`, `MonitorConfig`) and enums |
| `src/price_drop_monitor/extractors.py` | Create | Pure `jsonld`, `dom` (`selectolax.lexbor`), and `json_api` price extractors with fail-closed error handling |
| `src/price_drop_monitor/storage.py` | Create | Local CSV watchlist reader, history CSV appender, and injectable `GoogleSheetsAdapter` (Tab 1 read + Tab 2 append) |
| `src/price_drop_monitor/alerts.py` | Create | Threshold alert evaluator, consolidated message formatter, injectable `EmailNotifier` (SMTP) and `WebhookNotifier` (HTTP POST) |
| `src/price_drop_monitor/pipeline.py` | Create | Fault-isolated item check runner, offline/live fetcher, history sync, alert dispatch, and atomic JSON report writer |
| `run.py` | Create | Single CLI entry point (`--offline`, `--watchlist`, `--history`, `--output`, `--send-alerts`) |
| `validate.py` | Create | Draft 2020-12 JSON Schema validation CLI and helper (`validate_report_file`) |
| `requirements.txt` | Create | Pinned public PyPI production dependencies (`httpx`, `jsonschema`, `pydantic`, `selectolax`) |
| `.github/workflows/price_monitor.yml` | Create | Daily scheduled GitHub Actions workflow (`0 13 * * *` UTC + `workflow_dispatch`) |
| `README.md` | Update | Client & portfolio documentation covering CSV/Google Sheets setup, alerting, offline tests, and GitHub Actions |
| `tests/test_parser.py` | Update | Flat offline unit tests for all extractors, sanitizer, and fail-closed Pydantic model validation |
| `tests/test_pipeline.py` | Create | Flat offline unit & integration tests for threshold alerts, SMTP/webhook/Sheets doubles, fault isolation, and schema validation |

---

## 5. Price Extraction, History & Alerting Logic

### 5.1 Pure Extraction Rules (`src/price_drop_monitor/extractors.py`)

1. **`extract_jsonld_price(html: str, default_currency: str = "USD") -> ExtractedPrice`:**
   - Parse HTML with `LexborHTMLParser(html)` and iterate over all `script[type="application/ld+json"]` nodes.
   - Parse node text via `json.loads`. Candidate product nodes are resolved from:
     - Top-level dict where `@type` is `"Product"` (or a list containing `"Product"`).
     - Top-level list of dicts containing a `"Product"` dict.
     - `@graph` list inside a dict containing a `"Product"` dict.
   - Extract `offers` from the `Product` dict (`dict` or non-empty `list[dict]`).
   - Extract raw price from `offer.get("price")` (or `offer.get("lowPrice")`), convert to finite `float >= 0` (rounded to 2 decimal places), and read `offer.get("priceCurrency")` (normalized to 3-character uppercase string, defaulting to `default_currency`).
   - If no `script[type="application/ld+json"]` node exists, JSON parsing fails, or no finite numeric price `>= 0` is found, return `ExtractedPrice(price=None, currency=default_currency, status=CheckStatus.ERROR, error_message=...)`.

2. **`extract_dom_price(html: str, default_currency: str = "USD") -> ExtractedPrice`:**
   - Parse HTML with `LexborHTMLParser(html)` and locate `.product-card .price-current` (fallback to `.price-current`).
   - Read raw price string from the element's `data-price` attribute if present and non-empty; otherwise extract from `element.text(strip=True)`.
   - Resolve currency from `.product-card .price-currency` (or `.price-currency`) text if a 3-letter code is present; otherwise infer from currency prefix/symbol in the text (`$` -> `"USD"`, `€` -> `"EUR"`, `£` -> `"GBP"`) or fall back to `default_currency`.
   - Strip currency symbols and thousand separators, then parse finite `float >= 0`.
   - If the price element is absent or the parsed value is not a finite non-negative number, return `ExtractedPrice(price=None, currency=default_currency, status=CheckStatus.ERROR, error_message=...)`.

3. **`extract_json_api_price(payload: str, default_currency: str = "USD") -> ExtractedPrice`:**
   - Parse JSON via `json.loads(payload)` and verify root is a `dict`.
   - Reject booleans (`isinstance(raw_price, bool)`) and require `price` to be a finite `int | float | str` convertible to `float >= 0`.
   - If `in_stock is False` and `price is None`, or if `price` is missing/invalid, return `ExtractedPrice(price=None, currency=..., status=CheckStatus.ERROR, error_message=...)`.
   - Normalize `currency` (3-character uppercase string, fallback `default_currency`) and return `ExtractedPrice(price=round(val, 2), currency=currency, status=CheckStatus.OK)`.

4. **`extract_price(raw_content: str, source_type: SourceType, default_currency: str = "USD") -> ExtractedPrice`:**
   - Pure dispatcher routing `SourceType.JSONLD`, `SourceType.DOM`, and `SourceType.JSON_API`.

### 5.2 Storage & Google Sheets Sync (`src/price_drop_monitor/storage.py`)

1. **Local CSV Watchlist (`load_watchlist_csv(path: Path) -> list[WatchlistItem]`):**
   - Reads UTF-8 CSV via `csv.DictReader`.
   - Normalizes empty `old_price` or `fixture_file` strings to `None` before constructing `WatchlistItem`.
2. **Local CSV History Appender (`append_history_csv(path: Path, results: Sequence[PriceCheckResult]) -> None`):**
   - Ensures parent directory exists.
   - Writes CSV header `checked_at,product_id,name,category,current_price,target_price,currency,status,error_message,url` when creating a new or empty file.
   - Appends one row per `PriceCheckResult`. When `status == "error"`, `current_price` is written as `""` (empty cell representing `null`) while `status` is `"error"` and `error_message` contains the explicit failure reason.
3. **Google Sheets REST Adapter (`GoogleSheetsAdapter`):**
   - Uses Google Sheets REST API v4 over an injectable `httpx.Client` with `Authorization: Bearer {api_token}`:
     - `fetch_watchlist() -> list[WatchlistItem]`: `GET https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{watchlist_range}` (default range `Watchlist!A1:I500`), mapping header + data rows into `WatchlistItem` models.
     - `append_history(results: Sequence[PriceCheckResult]) -> None`: `POST https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{history_range}:append?valueInputOption=USER_ENTERED` (default range `PriceHistory!A:J`) with JSON body `{"values": [...]}` matching the 10-column history schema.

### 5.3 Alert Evaluation & Consolidated Formatting (`src/price_drop_monitor/alerts.py`)

1. **Threshold Rule (`evaluate_alerts(results: Sequence[PriceCheckResult]) -> list[PriceAlert]`):**
   - An alert is triggered if and only if:
     `result.status == CheckStatus.OK and result.current_price is not None and result.current_price < result.target_price`
   - Items with `status == "error"` or `current_price >= target_price` NEVER trigger an alert.
   - `savings` calculation:
     - When `old_price is not None`: `round(max(0.0, old_price - current_price), 2)`
     - When `old_price is None`: `None`
2. **Consolidated Message Format (`format_consolidated_alert(alerts: Sequence[PriceAlert]) -> str`):**
   - Produces one consolidated plain-text report listing the count of triggered deals and, for each alert:
     - `name` (`product_id`)
     - `new_price` vs `target_price` (and `old_price` when present) in `currency`
     - `savings` amount
     - `url`
3. **Injectable Notifiers:**
   - `EmailNotifier`: Accepts `smtp_host`, `smtp_port`, `smtp_user`, `smtp_app_password`, `to_email`, and injectable `smtp_factory: Callable[[str, int], Any] = smtplib.SMTP`. Connects, invokes `starttls()`, authenticates if credentials are provided, and sends an `EmailMessage` with subject `"Price Drop Alert: {len(alerts)} item(s) below target"`.
   - `WebhookNotifier`: Accepts `webhook_url` and injectable `client: httpx.Client | None = None`. Sends `POST` with JSON payload `{"text": message, "content": message, "alert_count": len(alerts)}`.

### 5.4 Pipeline Fault Isolation, CLI, Schema Gate & Automation (`src/price_drop_monitor/pipeline.py`, `run.py`, `validate.py`, `.github/workflows/price_monitor.yml`, `requirements.txt`)

- **Fault Isolation (`src/price_drop_monitor/pipeline.py`):** Each item in the watchlist is fetched and parsed inside an isolated `try...except Exception as exc` boundary. Network errors, HTTP non-200 responses, missing fixture files, or parser failures produce a fail-closed `PriceCheckResult(status=CheckStatus.ERROR, current_price=None, triggered_alert=False, error_message=str(exc), ...)` without aborting the remaining watchlist items.
- **Offline Mode (`--offline` in `run.py`):** Resolves each item's content from `config.fixtures_dir / item.fixture_file`. If `config.watchlist_path` does not exist on disk in `--offline` mode, reads `config.fixtures_dir / "watchlist_targets.csv"` and seeds `data/watchlist.csv` when writing default output.
- **CLI Entry Point (`run.py`):** Parses `--offline`, `--watchlist`, `--history`, `--output`, and `--send-alerts`, executes `run_pipeline(config)`, and exits `0`.
- **Schema Validator (`validate.py`):** Loads `docs/price_report.schema.json` and validates `data/latest_report.json` with `jsonschema.Draft202012Validator(schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)`. Exits with code `0` on success and `1` on validation or file errors.
- **GitHub Actions Workflow (`.github/workflows/price_monitor.yml`):** Triggers daily via `cron: "0 13 * * *"` (13:00 UTC / 08:00 AM US Eastern) and `workflow_dispatch`; sets up Python 3.11, installs dependencies from `requirements.txt`, executes `python run.py --send-alerts` and `python validate.py`, and commits updated `data/price_history.csv` and `data/latest_report.json`.
- **Pinned Dependencies (`requirements.txt`):** Pinned public PyPI packages (`httpx`, `jsonschema`, `pydantic`, `selectolax`) with zero internal workspace references.

---

## 6. Test Spec & Offline Fixture Contract

All tests are flat pytest functions (`def test_*() -> None:`, zero `class Test*` groupings) and execute 100% offline with injected doubles (`httpx.MockTransport`, fake SMTP callable).

### `tests/test_parser.py`

1. `test_offline_fixtures_present`: Verifies all 6 files in `tests/fixtures/` exist and are non-empty.
2. `test_html_fixtures_contain_expected_nodes`: Verifies DOM/JSON-LD structure of sanitized HTML fixtures.
3. `test_sanitize_html_preserves_jsonld_and_strips_tracking`: Verifies `scripts/sanitize_fixture.py` keeps `application/ld+json` and strips executable scripts, `utm_*` params, and auth attributes.
4. `test_extract_jsonld_top_level_product`: Parses `cycling_garmin_edge.html` -> `price=369.99`, `currency="USD"`, `status=CheckStatus.OK`.
5. `test_extract_jsonld_graph_offer_list`: Parses `cycling_gp5000_tire.html` -> `price=62.50`, `currency="USD"`, `status=CheckStatus.OK`.
6. `test_extract_jsonld_root_array_and_malformed_fallback`: Verifies root-list JSON-LD product extraction and fail-closed `status=CheckStatus.ERROR` when JSON-LD is malformed or lacks a valid price.
7. `test_extract_dom_price_with_data_attribute`: Parses `hardware_nvme_ssd.html` -> `price=149.99`, `currency="USD"`, `status=CheckStatus.OK`.
8. `test_extract_dom_price_text_fallback`: Parses HTML with text-only `$89.50` price (no `data-price` attribute) -> `price=89.50`, `currency="USD"`, `status=CheckStatus.OK`.
9. `test_extract_dom_missing_price_fails_closed`: Parses `hardware_missing_price.html` -> `price=None`, `status=CheckStatus.ERROR`, non-empty `error_message`.
10. `test_extract_json_api_price_ok_and_error`: Parses `software_deal_api.json` -> `price=199.00`, `currency="USD"`, `status=CheckStatus.OK`; verifies missing/invalid price or malformed JSON returns `status=CheckStatus.ERROR` and `price=None`.
11. `test_price_check_result_fail_closed_validation`: Verifies `PriceCheckResult` model validator rejects `status="error"` with non-null `current_price`, `triggered_alert=True`, or blank `error_message`.

### `tests/test_pipeline.py`

1. `test_evaluate_alerts_strict_threshold_and_savings`: Verifies alerts trigger only when `status == "ok"` and `current_price < target_price` (never when `current_price == target_price`, `current_price > target_price`, or `status == "error"`), and verifies `savings` calculation.
2. `test_format_consolidated_alert_contains_required_fields`: Verifies consolidated alert text contains `name`, `old_price`, `new_price`, `target_price`, `savings`, and `url` for all triggered alerts.
3. `test_email_and_webhook_notifiers_with_injected_doubles`: Verifies `EmailNotifier` (with injected SMTP double) and `WebhookNotifier` (with `httpx.MockTransport`) send the consolidated payload without opening network sockets.
4. `test_google_sheets_adapter_sync_with_mock_transport`: Verifies `GoogleSheetsAdapter.fetch_watchlist()` (Tab 1 `GET`) and `GoogleSheetsAdapter.append_history()` (Tab 2 `POST :append`, including fail-closed error rows) via `httpx.MockTransport`.
5. `test_pipeline_offline_end_to_end_and_schema_validation`: Runs `run_pipeline` in `--offline` mode against `tests/fixtures/watchlist_targets.csv` using `tmp_path`, asserts 5 results (4 `"ok"`, 1 `"error"` for `keychron-q1-pro`), 4 triggered alerts, CSV history creation with 5 rows, and 100% validation of generated `latest_report.json` against `docs/price_report.schema.json`.
6. `test_pipeline_fault_isolation_on_fetch_error`: Injects an `httpx.MockTransport` that raises an HTTP 503 / transport error on one URL and returns valid HTML on the second URL; verifies the pipeline records `status="error"` for the failed item and `status="ok"` for the healthy item.

---

## 7. Machine-Checkable Acceptance Criteria

| AC ID | Criterion | Verification Command |
| :--- | :--- | :--- |
| **AC-1** | Offline pipeline generates `data/latest_report.json` validating 100% against `docs/price_report.schema.json` | `just run --offline; and just validate` |
| **AC-2** | Missing or unparsable price fixture records `status="error"`, `current_price=null`, and non-empty `error_message` | `just test -k "missing or error"` |
| **AC-3** | Threshold alert triggers strictly below `target_price` and formats consolidated email/webhook notifications | `just test -k "alert"` |
| **AC-4** | Storage, Google Sheets Tab 1/Tab 2 REST sync, and fault-isolated pipeline tests pass 100% offline via injected doubles | `just test -k "sheets or pipeline"` |
| **AC-5** | Zero internal engine imports (`extraction_infra`, `target_ops`) exist in `src/` or `tests/` | `fish -c "not rg 'extraction_infra      | target_ops' src tests"` |
| **AC-6** | Canonical verification gate (`ruff`, `basedpyright`, `gitleaks`, `pytest`) passes with zero errors | `just check` |

---

## 8. Non-Goals & Out of Scope

- **No Internal Engine Dependencies:** Never import or depend on `extraction-infra` or `target-ops`; all dependencies are 100% public PyPI packages.
- **No Live Network Calls in Tests:** Never connect to external retail URLs, live Google Sheets endpoints, live SMTP servers, or live webhooks during `pytest` or `just check`.
- **No Anti-Bot / CAPTCHA Bypass:** No browser automation (Playwright/Selenium), proxy rotation, or CAPTCHA/Turnstile solving.
- **No External Database Server:** No PostgreSQL, MySQL, SQLite, or Redis daemon; persistence is strictly CSV files, JSON snapshot reports, and optional Google Sheets REST sync.

---

## 9. Verification Gate Execution

```fish
just fmt
just check
just run --offline
just validate
```
