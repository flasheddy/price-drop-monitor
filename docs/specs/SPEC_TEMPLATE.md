# Engineering Specification Template

> Keep `Status: DRAFT` until Operator review is complete. Set `APPROVED` only after Operator sign-off.

- **Status:** DRAFT | APPROVED | COMPLETED | SUPERSEDED
- **Owner:** [Author Name / Agent]
- **Target Package(s):** `src/price_drop_monitor`
- **Inputs:** `docs/project_brief.md`, `docs/price_report.schema.json`, `tests/fixtures/`
- **Core Invariant:** Zero internal engine leakage; 100% public PyPI; offline deterministic tests; fail-closed price parsing (`status="error"` instead of guessing).

---

## 1. Objective & Scope

[What this feature/pipeline delivers, watchlist sources covered, extraction strategies (`jsonld`, `dom`, `json_api`), history output (`CSV` + optional Google Sheets), and alerting (`SMTP` + `Webhook`).]

---

## 2. Extraction Targets & Sanitized Fixture Contract

[Document each fixture in `tests/fixtures/`, extraction strategy (`jsonld` / `dom` / `json_api`), expected parsed values, and explicit error-case behavior.]

---

## 3. Data Contracts & Schema Mapping

[Define Pydantic V2 models mapping directly to `docs/price_report.schema.json`, enum definitions (`ProductCategory`, `SourceType`, `CheckStatus`), and config defaults.]

---

## 4. Module Layout & Diff Boundaries

| Module Path | Action | Responsibility |
| :--- | :--- | :--- |
| `src/price_drop_monitor/models.py` | Create | Pydantic schemas, enums, and pipeline configuration |
| `src/price_drop_monitor/extractors.py` | Create | Pure JSON-LD, DOM (`selectolax`), and JSON API price extractors |
| `src/price_drop_monitor/storage.py` | Create | Watchlist CSV reader, history CSV appender, Google Sheets sync |
| `src/price_drop_monitor/alerts.py` | Create | Threshold detector, SMTP email formatter, and webhook notifier |
| `src/price_drop_monitor/pipeline.py` | Create | Orchestration, fault isolation, and JSON report writer |
| `run.py` | Create | Single CLI entry point |
| `validate.py` | Create | Draft 2020-12 JSON Schema validation entry point |
| `requirements.txt` | Create | Pinned public PyPI dependencies for deployment |
| `.github/workflows/price_monitor.yml` | Create | Daily scheduled GitHub Actions workflow |
| `README.md` | Update | Client/portfolio runbook, Google Sheets & alert setup docs |

---

## 5. Price Extraction, History & Alerting Logic

[Exact parsing fallback rules, fail-closed missing-price handling (`status="error"`), `triggered_alert` rule (`current_price < target_price`), and consolidated alert body layout.]

---

## 6. Test Spec & Offline Fixture Contract

[Describe offline unit tests in `tests/test_parser.py` and `tests/test_pipeline.py`, injected HTTP/SMTP/Sheets/Webhook doubles, and schema validation checks.]

---

## 7. Machine-Checkable Acceptance Criteria

| AC ID | Criterion | Verification Command |
| :--- | :--- | :--- |
| **AC-1** | Output validates against `docs/price_report.schema.json` | `just validate` |
| **AC-2** | Missing price fixture records `status="error"` fail-safe | `just test -k "missing or error"` |
| **AC-3** | Threshold alert triggers only below `target_price` | `just test -k "alert"` |
| **AC-4** | Zero internal engine imports | `fish -c "not rg 'extraction_infra' src tests"` |
| **AC-5** | Canonical verification gate passes | `just check` |

---

## 8. Non-Goals & Out of Scope

[Explicitly state what is not built (no paid APIs, no credential sharing, no database server, no anti-bot challenge solving).]

---

## 9. Verification Gate Execution

```fish
just check
just run --offline
just validate
```
