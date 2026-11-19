"""Pydantic V2 domain models and configuration for price-drop-monitor."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProductCategory(StrEnum):
    """Supported product categories in the watchlist."""

    CYCLING_GEAR = "cycling_gear"
    TECH_HARDWARE = "tech_hardware"
    SOFTWARE_DEAL = "software_deal"


class SourceType(StrEnum):
    """Supported price extraction strategies."""

    JSONLD = "jsonld"
    DOM = "dom"
    JSON_API = "json_api"


class CheckStatus(StrEnum):
    """Outcome of a single price check."""

    OK = "ok"
    ERROR = "error"


class WatchlistItem(BaseModel):
    """One tracked product read from CSV or Google Sheets Tab 1."""

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
    """Normalized output of a pure price extractor."""

    model_config = ConfigDict(extra="forbid")

    price: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    status: CheckStatus
    error_message: str | None = None


class PriceCheckResult(BaseModel):
    """Persisted result of checking one watchlist item."""

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
    """One strictly-below-target deal included in the consolidated alert."""

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
    """Daily run snapshot written to ``data/latest_report.json``."""

    model_config = ConfigDict(extra="forbid")

    generated_at: str = Field(min_length=1)
    results: list[PriceCheckResult]
    alerts: list[PriceAlert]


class MonitorConfig(BaseModel):
    """Runtime configuration assembled by the CLI from flags and environment."""

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
