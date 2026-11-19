# Project Brief — Price Drop Monitor (Cycling Gear, Tech Hardware & Software Deals)

## 1. Objective

Build a small, reliable, zero-hosting-cost Python price-monitoring pipeline that checks a watchlist of tracked products once per day (default: 08:00 AM US Eastern / `0 13 * * *` UTC on GitHub Actions), appends timestamped prices (or explicit parse-error records) into a persistent price history log, validates the latest run report against a strict Draft 2020-12 JSON Schema, and sends a single consolidated price-drop alert when one or more items fall below their configured target price.

Designed for dual use:

1. **Personal daily deal monitor** for cycling gear, tech hardware, and software deals.
2. **Public Modality B Upwork portfolio showcase** demonstrating production CSV/Google Sheets Tab 1 -> Tab 2 sync, JSON-LD + DOM + JSON API extraction, fail-closed error tracking, SMTP Email + Webhook alerting, and automated GitHub Actions scheduling.

---

## 2. Categories & Extraction Strategies

- **Categories (`enum`):**
  - `cycling_gear` — GPS bike computers, performance tires, drivetrains, lights, accessories.
  - `tech_hardware` — NVMe SSDs, mechanical keyboards, monitors, CPUs, peripherals.
  - `software_deal` — IDE suites, developer licenses, annual SaaS deals.
- **Extraction Strategies (`source_type`):**
  1. `jsonld`: Parse `<script type="application/ld+json">` for `schema.org/Product` (`offers.price`, `offers.priceCurrency`, `offers.availability`). Handles arrays or `@graph` containers gracefully.
  2. `dom`: Fallback CSS selector extraction via `selectolax.lexbor` (e.g., `.product-card .price-current[data-price]`, text currency parsing).
  3. `json_api`: Direct JSON endpoint extraction (`price`, `currency`, `in_stock`).
- **Fail-Closed Price Rule:** If no valid numeric price can be extracted (`>= 0`), record `status="error"`, `current_price=null`, and an explicit `error_message` instead of guessing `0` or a blank cell.

---

## 3. Data Flow (Watchlist -> History -> Alert)

1. **Watchlist Input (Tab 1):** Read target items (`product_id`, `name`, `category`, `url`, `target_price`, `old_price`, `currency`, `source_type`, optional `fixture_file`) from local CSV (`data/watchlist.csv`) or optional Google Sheets Tab 1 (`GOOGLE_SHEETS_SPREADSHEET_ID` + `GOOGLE_SHEETS_API_TOKEN`).
2. **Price Check:** Fetch live (or load offline fixture when `--offline` is set) and extract current price + currency.
3. **History Append (Tab 2):** Append daily check rows (`checked_at`, `product_id`, `name`, `category`, `current_price`, `target_price`, `currency`, `status`, `error_message`, `url`) to `data/price_history.csv` and optionally Google Sheets Tab 2.
4. **Consolidated Price-Drop Alerting:** Trigger alerts strictly when `status == "ok"` and `current_price < target_price`. Build one consolidated alert payload showing `product name`, `old_price`, `new_price`, `target_price`, `savings`, and `url`, deliverable via:
   - **SMTP Email** (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_APP_PASSWORD`, `ALERT_TO_EMAIL`), and/or
   - **Webhook / ntfy Push** (`WEBHOOK_ALERT_URL`).
5. **Schema-Validated Snapshot:** Write `data/latest_report.json` validating 100% against `docs/price_report.schema.json`.

---

## 4. Deliverables

- Standalone Python 3.11+ project (`uv sync` / `pip install -r requirements.txt`) with zero internal workspace dependencies.
- `run.py` CLI supporting `--offline`, `--watchlist`, `--history`, `--output`, `--send-alerts`.
- `validate.py` CLI checking `data/latest_report.json` against `docs/price_report.schema.json`.
- `requirements.txt` (pinned production dependencies) and `.env.example` (no secrets in code).
- `.github/workflows/price_monitor.yml` (daily cron `0 13 * * *` UTC + manual `workflow_dispatch`, run + validate + commit updated history).
- `README.md` covering setup, Google Sheets Tab 1/Tab 2 configuration, adding products or target prices, email/webhook alerts, and test runs.

---

## 5. Acceptance Criteria

1. `data/latest_report.json` validates 100% against `docs/price_report.schema.json`.
2. Missing-price pages fail closed (`status="error"`, `current_price=null`, non-empty `error_message`, never guessed).
3. Items with `current_price < target_price` produce accurate alert objects (`savings = old_price - new_price` when `old_price` is known) and format a consolidated email/webhook body; items `>= target_price` do not trigger false alerts.
4. Zero live network traffic during tests (`just check` passes 100% offline with 0 secret leaks, 0 ruff errors, 0 basedpyright diagnostics).
