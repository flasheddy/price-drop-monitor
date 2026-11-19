"""Local CSV watchlist/history persistence and Google Sheets REST sync."""

import csv
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from .models import PriceCheckResult, WatchlistItem

WATCHLIST_COLUMNS: tuple[str, ...] = (
    "product_id",
    "name",
    "category",
    "url",
    "target_price",
    "old_price",
    "currency",
    "source_type",
    "fixture_file",
)

HISTORY_COLUMNS: tuple[str, ...] = (
    "checked_at",
    "product_id",
    "name",
    "category",
    "current_price",
    "target_price",
    "currency",
    "status",
    "error_message",
    "url",
)


def _clean_mapping(row: dict[str, Any]) -> dict[str, Any]:
    """Keep known watchlist columns, strip cells, and normalize optional blanks."""
    data: dict[str, Any] = {}
    for column in WATCHLIST_COLUMNS:
        raw_value = row.get(column)
        value = "" if raw_value is None else str(raw_value).strip()
        if column in {"old_price", "fixture_file"}:
            data[column] = value or None
        else:
            data[column] = value
    if not data["currency"]:
        data["currency"] = "USD"
    return data


def _watchlist_item(row: dict[str, Any]) -> WatchlistItem:
    return WatchlistItem.model_validate(_clean_mapping(row))


def load_watchlist_csv(path: Path) -> list[WatchlistItem]:
    """Read the local watchlist CSV into validated models."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        items: list[WatchlistItem] = []
        for row in reader:
            if not any(str(value or "").strip() for value in row.values()):
                continue
            items.append(_watchlist_item(row))
    return items


def _history_row(result: PriceCheckResult) -> dict[str, str]:
    current_price = "" if result.current_price is None else f"{result.current_price:.2f}"
    return {
        "checked_at": result.checked_at,
        "product_id": result.product_id,
        "name": result.name,
        "category": result.category.value,
        "current_price": current_price,
        "target_price": f"{result.target_price:.2f}",
        "currency": result.currency,
        "status": result.status.value,
        "error_message": result.error_message or "",
        "url": result.url,
    }


def append_history_csv(path: Path, results: Sequence[PriceCheckResult]) -> None:
    """Append one row per check result, writing the header for a new or empty file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_COLUMNS, lineterminator="\n")
        if needs_header:
            writer.writeheader()
        for result in results:
            writer.writerow(_history_row(result))


class GoogleSheetsAdapter:
    """Minimal Google Sheets REST v4 adapter for Tab 1 watchlists and Tab 2 history."""

    BASE_URL = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(
        self,
        spreadsheet_id: str,
        api_token: str,
        client: httpx.Client | None = None,
        *,
        watchlist_range: str = "Watchlist!A1:I500",
        history_range: str = "PriceHistory!A:J",
    ) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.api_token = api_token
        self.watchlist_range = watchlist_range
        self.history_range = history_range
        self._client = client
        self._owns_client = client is None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=30.0)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}"}

    def close(self) -> None:
        """Close an internally created HTTP client."""
        if self._owns_client and self._client is not None:
            self._client.close()

    def fetch_watchlist(self) -> list[WatchlistItem]:
        """Read Tab 1 and map its header row plus data rows into watchlist models."""
        url = f"{self.BASE_URL}/{self.spreadsheet_id}/values/{self.watchlist_range}"
        response = self._http().get(url, headers=self._headers())
        response.raise_for_status()
        payload: Any = response.json()
        values = payload.get("values", []) if isinstance(payload, dict) else []
        if not isinstance(values, list) or not values:
            return []

        header_row = values[0]
        if not isinstance(header_row, list):
            return []
        headers = [str(cell).strip() for cell in header_row]

        items: list[WatchlistItem] = []
        for row in values[1:]:
            if not isinstance(row, list):
                continue
            record: dict[str, Any] = {}
            for index, header in enumerate(headers):
                record[header] = row[index] if index < len(row) else ""
            if not any(str(value or "").strip() for value in record.values()):
                continue
            items.append(_watchlist_item(record))
        return items

    def append_history(self, results: Sequence[PriceCheckResult]) -> None:
        """Append Tab 2 rows using the 10-column history schema."""
        url = f"{self.BASE_URL}/{self.spreadsheet_id}/values/{self.history_range}:append"
        rows = [
            [
                result.checked_at,
                result.product_id,
                result.name,
                result.category.value,
                "" if result.current_price is None else f"{result.current_price:.2f}",
                f"{result.target_price:.2f}",
                result.currency,
                result.status.value,
                result.error_message or "",
                result.url,
            ]
            for result in results
        ]
        response = self._http().post(
            url,
            params={"valueInputOption": "USER_ENTERED"},
            headers=self._headers(),
            json={"values": rows},
        )
        response.raise_for_status()
