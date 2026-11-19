# Price Drop Monitor

Standalone daily price-drop monitoring pipeline for cycling gear, tech hardware, and software
deals. It reads a watchlist, checks one price per product through JSON-LD, DOM, or JSON API
extraction, appends an immutable history row, validates a schema-strict JSON snapshot, and sends
one consolidated email/webhook alert when items fall strictly below target.

- **Zero hosting cost:** runs on GitHub Actions at `0 13 * * *` UTC (08:00 AM US Eastern).
- **Zero internal engines:** 100% public PyPI dependencies; no private workspace packages.
- **Offline deterministic tests:** every parser, storage, alert, and pipeline test runs against
  sanitized fixtures with injected HTTP/SMTP doubles.
- **Fail-closed prices:** a missing or unparsable price is recorded as `status="error"` with
  `current_price=null` and an explicit `error_message`; the pipeline never guesses `0`, the old
  price, or the target price.

---

## 1. Daily Flow

```text
Watchlist (CSV or Google Sheets Tab 1)
        |
        v
Fetch live URL or sanitized offline fixture
        |
        v
Extract price (jsonld | dom | json_api)  -- failure --> status=error, price=null
        |
        v
Append history CSV + optional Google Sheets Tab 2
        |
        v
Evaluate strict current_price < target_price
        |
        v
Write data/latest_report.json -> validate against docs/price_report.schema.json
        |
        v
Send one consolidated SMTP email and/or webhook alert
```

## 2. Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for the development workflow, or plain `pip` in CI
- Optional: a Google Sheets spreadsheet, SMTP credentials, and/or a webhook URL

Production dependencies are pinned in `requirements.txt` (generated with
`uv export --format requirements-txt --no-dev`): `httpx`, `jsonschema`, `pydantic`, and
`selectolax`.

## 3. Quickstart

```fish
uv sync
just test
just run --offline
just validate
```

`--offline` reads `tests/fixtures/watchlist_targets.csv` when `data/watchlist.csv` does not exist,
seeds `data/watchlist.csv`, and resolves every product from `tests/fixtures/`. No network request
is made.

The CLI reads configuration from process environment variables; it does not load `.env`
automatically. Copy values from `.env.example` into your shell (`set -gx NAME value` in fish) or
export them through your scheduler/secrets manager.

After the first run:

```text
data/
├── watchlist.csv        # tracked products (committed in normal use)
├── price_history.csv    # append-only daily history
└── latest_report.json   # latest validated run snapshot
```

## 4. Watchlist Configuration (Tab 1)

Edit `data/watchlist.csv` or point the CLI at another file:

```fish
just run --watchlist path/to/watchlist.csv
```

Columns:

| Column         | Required | Example                              | Notes                                                    |
| :------------- | :------- | :----------------------------------- | :------------------------------------------------------- |
| `product_id`   | yes      | `garmin-edge-840`                    | Stable unique ID used in history and alerts.             |
| `name`         | yes      | `Garmin Edge 840 Cycling Computer`   | Human-readable name, max 200 chars.                      |
| `category`     | yes      | `cycling_gear`                       | `cycling_gear`, `tech_hardware`, or `software_deal`.     |
| `url`          | yes      | `https://example.com/item`           | Must start with `http://` or `https://`.                 |
| `target_price` | yes      | `399.00`                             | Alert only when the current price is strictly lower.     |
| `old_price`    | no       | `449.99`                             | Used to calculate savings; blank is allowed.             |
| `currency`     | no       | `USD`                                | Three-letter code; defaults to `USD`.                    |
| `source_type`  | yes      | `jsonld`                             | `jsonld`, `dom`, or `json_api`.                          |
| `fixture_file` | no       | `cycling_garmin_edge.html`           | Required only for offline fixture checks.                |

Example:

```csv
product_id,name,category,url,target_price,old_price,currency,source_type,fixture_file
garmin-edge-840,Garmin Edge 840 Cycling Computer,cycling_gear,https://example.com/cycling/garmin-edge-840,399.00,449.99,USD,jsonld,cycling_garmin_edge.html
samsung-990-pro-2tb,Samsung 990 PRO 2TB PCIe 4.0 NVMe SSD,tech_hardware,https://example.com/hardware/samsung-990-pro-2tb,160.00,179.99,USD,dom,hardware_nvme_ssd.html
jetbrains-toolbox-deal,JetBrains All Products Pack Annual Deal,software_deal,https://example.com/software/jetbrains-all-products,220.00,289.00,USD,json_api,software_deal_api.json
```

Rows are validated with Pydantic. Unknown columns, malformed URLs, non-positive targets, and
invalid categories fail the run before any checks begin.

### Extraction strategies

- **`jsonld`** — reads `script[type="application/ld+json"]` and resolves a schema.org `Product`
  from a top-level object, a root array, or an `@graph` container. `offers` may be one object or a
  list. Uses `price`, then `lowPrice`, plus `priceCurrency`.
- **`dom`** — reads `.product-card .price-current` (or `.price-current`) from
  `selectolax.lexbor`; prefers `data-price`, otherwise parses the text. Currency comes from
  `.price-currency`, then `$`/`€`/`£`, then the watchlist default.
- **`json_api`** — parses a JSON object with `price`, optional `currency`, and optional
  `in_stock`.

If a finite non-negative price cannot be extracted, the result is fail-closed: `status="error"`,
`current_price=null`, `triggered_alert=false`, and a non-empty `error_message`. This behavior is
enforced both by the extractor and by a Pydantic model validator, so an invalid error row cannot be
written.

## 5. Google Sheets Sync (Optional)

The adapter uses Google Sheets REST API v4 with a bearer token. Configure:

```fish
set -gx GOOGLE_SHEETS_SPREADSHEET_ID your_spreadsheet_id
set -gx GOOGLE_SHEETS_API_TOKEN your_oauth_access_token
```

Create two tabs in the spreadsheet:

1. **`Watchlist`** — same nine columns as the CSV header above (`A1:I`).
2. **`PriceHistory`** — created automatically on first append, or pre-create the ten columns:
   `checked_at`, `product_id`, `name`, `category`, `current_price`, `target_price`, `currency`,
   `status`, `error_message`, `url`.

The adapter reads `Watchlist!A1:I500` and appends to `PriceHistory!A:J` with
`valueInputOption=USER_ENTERED`. When both environment variables are set and `--offline` is not
used, the Google Sheet replaces the local CSV as the watchlist source. History is still written
locally and is also appended to Tab 2. Error rows are appended with an empty `current_price` cell
and the explicit failure reason.

Authenticate with an OAuth access token that has Google Sheets scope (for example a service-account
access token shared to the spreadsheet). Tokens expire; refresh the GitHub secret on your normal
credential rotation schedule.

## 6. Alerting

An alert triggers **only** when all of these are true:

```text
status == "ok" AND current_price is not None AND current_price < target_price
```

Equality does **not** trigger. Errored rows never trigger. Savings is
`round(max(0.0, old_price - current_price), 2)` when `old_price` is known, otherwise `null`.
One consolidated message lists every triggered item with name, product ID, old price, new price,
target, savings, and URL.

### SMTP email

```fish
set -gx SMTP_HOST smtp.gmail.com
set -gx SMTP_PORT 587
set -gx SMTP_USER alerts@example.com
set -gx SMTP_APP_PASSWORD your_app_password
set -gx ALERT_TO_EMAIL you@example.com
```

`EmailNotifier` opens an SMTP session, calls `starttls()`, authenticates when a user and app
password are present, and sends an `EmailMessage` with subject
`Price Drop Alert: N item(s) below target`. For Gmail, use a Google app password rather than your
account password.

### Webhook / push

```fish
set -gx WEBHOOK_ALERT_URL https://ntfy.sh/your_private_topic
```

`WebhookNotifier` sends one JSON `POST` with:

```json
{"text": "...consolidated message...", "content": "...same message...", "alert_count": 2}
```

The `text` field suits ntfy-style services; the `content` field suits Discord/Slack-style
webhooks; custom endpoints receive both.

Alerts are dispatched only when `--send-alerts` is passed and at least one item triggers. Missing
notification configuration is a no-op, so the report and history still persist.

## 7. CLI Reference

| Command                                                          | Purpose                                                    |
| :--------------------------------------------------------------- | :--------------------------------------------------------- |
| `just run --offline`                                             | Run against sanitized local fixtures with no network.      |
| `just run`                                                       | Run live checks from the watchlist.                        |
| `just run --send-alerts`                                         | Run live and dispatch configured alerts.                   |
| `just run --watchlist data/watchlist.csv --history data/price_history.csv --output data/latest_report.json` | Override output paths. |
| `just validate`                                                  | Validate the report against the Draft 2020-12 schema.      |
| `just test`                                                      | Run the full offline pytest suite.                         |
| `just check`                                                     | Run `ruff`, `basedpyright`, `gitleaks`, and `pytest`.       |
| `just fmt`                                                       | Format Python sources with Ruff.                           |
| `just sanitize-fixture tests/fixtures/page.html`                 | Sanitize an HTML fixture in place and secret-scan it.      |

Direct Python equivalents are available when uv is not used:

```fish
python run.py --offline
python validate.py
```

`validate.py` exits `0` only when `data/latest_report.json` validates 100% against
`docs/price_report.schema.json`; validation or file errors exit `1`.

## 8. GitHub Actions

`.github/workflows/price_monitor.yml` runs daily at `0 13 * * *` UTC and on manual
`workflow_dispatch`. It:

1. installs Python 3.11 and `requirements.txt`;
2. runs `python run.py --send-alerts`;
3. runs `python validate.py`;
4. commits updated `data/price_history.csv` and `data/latest_report.json` back to the repository.

Add these repository secrets as needed:

| Secret                           | Purpose                                   |
| :------------------------------- | :---------------------------------------- |
| `GOOGLE_SHEETS_SPREADSHEET_ID`   | Optional Google Sheets watchlist/history. |
| `GOOGLE_SHEETS_API_TOKEN`        | Bearer token for Sheets REST v4.          |
| `SMTP_HOST`                      | SMTP server hostname.                     |
| `SMTP_PORT`                      | SMTP port, normally `587`.                |
| `SMTP_USER`                      | SMTP username / from address.             |
| `SMTP_APP_PASSWORD`              | SMTP app password.                        |
| `ALERT_TO_EMAIL`                 | Recipient for consolidated alerts.        |
| `WEBHOOK_ALERT_URL`              | ntfy/Discord/custom webhook endpoint.     |

Commit `data/watchlist.csv` when using the local CSV source, or configure the two Sheets secrets.
The workflow requires `contents: write` permission (already declared) to push updated data.

## 9. History and Report Contracts

`data/price_history.csv` is append-only with this header:

```text
checked_at,product_id,name,category,current_price,target_price,currency,status,error_message,url
```

For error rows, `current_price` is an empty cell while `status` is `error` and `error_message`
contains the reason. The latest snapshot contains:

```json
{
  "generated_at": "2026-11-19T13:00:00+00:00",
  "results": [],
  "alerts": []
}
```

`docs/price_report.schema.json` is a Draft 2020-12 schema with `additionalProperties: false` for
the report, result, and alert objects. `PriceReport.model_dump(mode="json")` is the only report
serializer, keeping Pydantic models and the schema in lockstep.

## 10. Offline Development and Testing

All tests are flat `def test_*` functions and run 100% offline:

- sanitized HTML/JSON/CSV fixtures in `tests/fixtures/`;
- `httpx.MockTransport` for Sheets, webhooks, and live-fetch simulation;
- a fake SMTP factory for email delivery;
- `tmp_path` for history, watchlist, and report files.

```fish
just test
just test -k "missing or error"
just test -k "alert"
just test -k "sheets or pipeline"
just check
```

`scripts/sanitize_fixture.py` preserves `application/ld+json` Product blocks while removing
executable scripts, tracking parameters (`utm_*` and friends), credential-like attributes, and
secret-shaped tokens. Use `just sanitize-fixture <file>` before committing any new real-world HTML
fixture.

## 11. Fail-Closed Guarantees

The model validator rejects:

- `status="error"` with a non-null `current_price`;
- `status="error"` with `triggered_alert=true`;
- `status="error"` with a blank or missing `error_message`;
- `status="ok"` with a null `current_price` or a non-null `error_message`.

Each watchlist item is fetched and parsed inside its own exception boundary. A network failure,
HTTP non-200 response, missing fixture, malformed JSON, missing DOM node, or validation failure
becomes an error row without aborting the rest of the watchlist. The run then continues to history,
report, and alerting.

## 12. Project Layout

```text
.
├── run.py                              # CLI entry point
├── validate.py                         # Draft 2020-12 schema gate
├── requirements.txt                    # pinned production dependencies
├── docs/
│   ├── price_report.schema.json
│   ├── project_brief.md
│   └── specs/price_drop_monitor.md
├── src/price_drop_monitor/
│   ├── models.py                       # Pydantic V2 models and enums
│   ├── extractors.py                   # jsonld / dom / json_api extractors
│   ├── storage.py                      # CSV + Google Sheets adapters
│   ├── alerts.py                       # threshold evaluator + notifiers
│   └── pipeline.py                     # fault-isolated orchestration
├── scripts/sanitize_fixture.py
├── tests/
│   ├── test_parser.py
│   ├── test_pipeline.py
│   └── fixtures/
└── .github/workflows/price_monitor.yml
```

## 13. Troubleshooting

- **`FileNotFoundError: data/watchlist.csv`** — run with `--offline` once to seed from the fixture,
  create your own CSV, or configure Google Sheets.
- **Every live item is an error** — retailers may block server-side requests, change markup, or
  return a non-200 status. Inspect `error_message`, update selectors/JSON paths, and verify the URL
  in a browser. This project does not bypass CAPTCHAs or anti-bot systems.
- **`Validation failed`** — run `just run --offline; and just validate` to isolate whether the
  failure is environmental or data-related; the schema error path is printed by `validate.py`.
- **No email arrived** — verify `--send-alerts`, all five SMTP variables, and that at least one item
  is strictly below target. Check the provider's app-password/2FA policy.
- **Google Sheets returned 401/403** — the bearer token is expired, lacks Sheets scope, or the
  spreadsheet has not been shared with the service account.
- **History has an empty `current_price`** — this is expected for fail-closed error rows; read
  `error_message` and `status` for the cause.

## 14. Scope and Limitations

This is a personal deal monitor and portfolio project. It does not use browser automation,
proxy rotation, CAPTCHA solving, or an external database. Persistence is limited to CSV files,
optional Google Sheets tabs, and JSON snapshots. Always respect retailer terms of service and
rate limits.
