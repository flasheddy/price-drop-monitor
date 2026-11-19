# AGENTS.md — price-drop-monitor (Modality B: Software Delivery & Personal Deal Monitor)

Standalone daily price-monitoring, history-logging, and threshold-alerting pipeline for cycling gear, tech hardware, and software deals.

## Operational Boundaries

- **Zero-Leakage Invariant:** This package must NEVER import, reference, or depend on internal engines (`extraction-infra`, `target-ops`). Dependencies are 100% public PyPI packages via `uv sync`.
- **Offline TDD Invariant:** All parser, history, sheet-adapter, and alert tests execute strictly offline against static fixtures in `tests/fixtures/`. Never make live network requests during tests or `just check`.
- **Fail-Closed Price Invariant:** When a product price cannot be found or fails parsing, record an explicit `status="error"` and `error_message` row in the history table/sheet (`current_price=null`) instead of guessing a blank or zero value.
- **JSON Schema Gate:** Output generated at `data/latest_report.json` must validate 100% against `docs/price_report.schema.json` via `python validate.py`.

## Spec Workflow (Phase 2)

Before implementing code:

1. Read `docs/project_brief.md`, `docs/price_report.schema.json`, and sanitized fixtures in `tests/fixtures/`.
2. Follow `docs/specs/SPEC_TEMPLATE.md` to author `docs/specs/price_drop_monitor.md` with `Status: APPROVED` (or `DRAFT` until reviewed).
3. Stop and request Operator review before implementing unapproved code.

## TDD Workflow (Phase 3 & 4)

1. Write failing unit tests in `tests/test_parser.py` and `tests/test_pipeline.py`.
2. Run `just test`; confirm expected RED failure.
3. Implement minimal passing code in `src/price_drop_monitor/`, `run.py`, and `validate.py`.
4. Run `just test`; confirm GREEN pass.
5. Pass canonical verification gate `just check`.

## Standard Commands

- `just check`: Full canonical gate (`ruff`, `basedpyright`, `gitleaks`, `pytest`).
- `just fmt`: Format Python and Markdown files via `uv run ruff format .`.
- `just test`: Run offline pytest suite.
- `just sanitize-fixture <file>`: Sanitize offline HTML fixtures in-place (preserving `application/ld+json` product blocks) and scan for secrets.
- `just run`: Run the price monitoring pipeline (`python run.py`).
- `just validate`: Validate `data/latest_report.json` against `docs/price_report.schema.json`.
