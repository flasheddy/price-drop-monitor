set shell := ["bash", "-uc"]

# Run full canonical verification
check: lint typecheck secrets test

# Fast linter check
lint:
    uv run ruff check .
    uv run ruff format --check .

# Auto-format source code and markdown code blocks
fmt:
    uv run ruff format .

# Strict typechecking
typecheck:
    uv run basedpyright

# Run offline test suite
test *args:
    uv run pytest {{ args }}

# Secret scan using gitleaks
secrets:
    gitleaks dir . --no-banner --redact --log-level warn

# Sanitize offline HTML fixture in-place and verify zero secrets
sanitize-fixture file:
    uv run python scripts/sanitize_fixture.py "{{ file }}"
    gitleaks dir "{{ file }}" --no-banner --redact --log-level warn

# Run price monitoring pipeline
run *args:
    uv run python run.py {{ args }}

# Validate output latest_report.json against schema
validate:
    uv run python validate.py
