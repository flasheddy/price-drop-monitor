"""Pure, fail-closed price extractors for JSON-LD, DOM, and JSON API sources."""

import json
import math
import re
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from .models import CheckStatus, ExtractedPrice, SourceType

_CURRENCY_CODE_RE = re.compile(r"^[A-Za-z]{3}$")
_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_SYMBOL_CURRENCIES: tuple[tuple[str, str], ...] = (
    ("$", "USD"),
    ("€", "EUR"),
    ("£", "GBP"),
)


def _currency_code(value: object) -> str | None:
    """Return a normalized 3-letter currency code, or None when absent/invalid."""
    if isinstance(value, str):
        candidate = value.strip().upper()
        if _CURRENCY_CODE_RE.fullmatch(candidate):
            return candidate
    return None


def _normalize_currency(value: object, default_currency: str) -> str:
    """Normalize a 3-letter currency code, falling back safely."""
    fallback = _currency_code(default_currency) or "USD"
    return _currency_code(value) or fallback


def _coerce_price(raw: object) -> float | None:
    """Convert a numeric or numeric-string value into a finite non-negative float."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        value = float(raw)
    elif isinstance(raw, str):
        cleaned = raw.strip().replace(",", "")
        cleaned = re.sub(r"^[€£$]\s*", "", cleaned)
        cleaned = re.sub(r"\s*[€£$]$", "", cleaned)
        cleaned = re.sub(r"^(?:USD|EUR|GBP)\s+", "", cleaned, flags=re.IGNORECASE)
        if not cleaned:
            return None
        try:
            value = float(cleaned)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return round(value, 2)


def _extract_number_from_text(text: str) -> float | None:
    """Find the first number in display text and coerce it (rejecting negatives)."""
    without_symbols = re.sub(r"[€£$]", "", text)
    match = _NUMBER_RE.search(without_symbols)
    if match is None:
        return None
    return _coerce_price(match.group(0))


def _is_product_type(raw_type: object) -> bool:
    if isinstance(raw_type, str):
        return raw_type.strip().lower() == "product"
    if isinstance(raw_type, list):
        return any(
            isinstance(entry, str) and entry.strip().lower() == "product" for entry in raw_type
        )
    return False


def _collect_product_nodes(payload: Any) -> list[dict[str, Any]]:
    """Resolve product dicts from a top-level object, root list, or @graph container."""
    nodes: list[dict[str, Any]] = []

    def add_node(candidate: object) -> None:
        if isinstance(candidate, dict) and _is_product_type(candidate.get("@type")):
            nodes.append(candidate)

    def add_graph(candidate: object) -> None:
        if not isinstance(candidate, dict):
            return
        graph = candidate.get("@graph")
        if isinstance(graph, list):
            for entry in graph:
                add_node(entry)

    if isinstance(payload, list):
        for entry in payload:
            add_node(entry)
            add_graph(entry)
    else:
        add_node(payload)
        add_graph(payload)
    return nodes


def _iter_offers(product: dict[str, Any]) -> list[dict[str, Any]]:
    offers = product.get("offers")
    if isinstance(offers, dict):
        return [offers]
    if isinstance(offers, list):
        return [entry for entry in offers if isinstance(entry, dict)]
    return []


def _error(message: str, currency: str = "USD") -> ExtractedPrice:
    return ExtractedPrice(
        price=None,
        currency=currency,
        status=CheckStatus.ERROR,
        error_message=message,
    )


def extract_jsonld_price(html: str, default_currency: str = "USD") -> ExtractedPrice:
    """Extract a schema.org Product offer price from ``application/ld+json`` blocks."""
    currency = _normalize_currency(default_currency, "USD")
    tree = LexborHTMLParser(html)
    scripts = tree.css('script[type="application/ld+json"]')
    if not scripts:
        return _error("No application/ld+json script found in HTML", currency)

    parse_errors: list[str] = []
    for script in scripts:
        raw_payload = script.text()
        if not raw_payload or not raw_payload.strip():
            continue
        try:
            payload = json.loads(raw_payload)
        except (json.JSONDecodeError, ValueError) as exc:
            parse_errors.append(str(exc))
            continue

        for product in _collect_product_nodes(payload):
            for offer in _iter_offers(product):
                raw_price = offer.get("price")
                if raw_price is None:
                    raw_price = offer.get("lowPrice")
                price = _coerce_price(raw_price)
                if price is None:
                    continue
                return ExtractedPrice(
                    price=price,
                    currency=_normalize_currency(offer.get("priceCurrency"), currency),
                    status=CheckStatus.OK,
                )

    if parse_errors:
        return _error(
            f"Malformed JSON-LD payload; no usable Product price: {parse_errors[-1]}",
            currency,
        )
    return _error("No finite non-negative Product offer price found in JSON-LD", currency)


def extract_dom_price(html: str, default_currency: str = "USD") -> ExtractedPrice:
    """Extract a price from ``.product-card .price-current`` via data attribute or text."""
    currency = _normalize_currency(default_currency, "USD")
    tree = LexborHTMLParser(html)
    element = tree.css_first(".product-card .price-current")
    if element is None:
        element = tree.css_first(".price-current")
    if element is None:
        return _error("Price element '.price-current' not found in HTML", currency)

    display_text = element.text(strip=True) or ""
    raw_attribute = element.attributes.get("data-price")
    if raw_attribute is not None and raw_attribute.strip():
        price = _coerce_price(raw_attribute)
    else:
        price = _extract_number_from_text(display_text)

    if price is None:
        return _error("Unable to parse a finite non-negative DOM price", currency)

    currency_element = tree.css_first(".product-card .price-currency")
    if currency_element is None:
        currency_element = tree.css_first(".price-currency")
    resolved_currency = (
        _currency_code(currency_element.text(strip=True)) if currency_element is not None else None
    )
    if resolved_currency is None:
        currency_text = (
            currency_element.text(strip=True) if currency_element is not None else None
        ) or ""
        for symbol, code in _SYMBOL_CURRENCIES:
            if symbol in display_text:
                resolved_currency = code
                break
        if resolved_currency is None:
            for symbol, code in _SYMBOL_CURRENCIES:
                if symbol in currency_text:
                    resolved_currency = code
                    break
    if resolved_currency is not None:
        currency = resolved_currency

    return ExtractedPrice(price=price, currency=currency, status=CheckStatus.OK)


def extract_json_api_price(payload: str, default_currency: str = "USD") -> ExtractedPrice:
    """Extract ``price``/``currency`` from a JSON API response body."""
    fallback_currency = _normalize_currency(default_currency, "USD")
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        return _error(f"Malformed JSON API payload: {exc}", fallback_currency)
    if not isinstance(data, dict):
        return _error("JSON API payload root must be an object", fallback_currency)

    currency = _normalize_currency(data.get("currency"), fallback_currency)
    raw_price = data.get("price")
    price = _coerce_price(raw_price)
    if price is None:
        if raw_price is None:
            if data.get("in_stock") is False:
                return _error("JSON API payload has no price and item is out of stock", currency)
            return _error("JSON API payload is missing a price", currency)
        return _error("JSON API price is not a finite non-negative number", currency)

    return ExtractedPrice(price=price, currency=currency, status=CheckStatus.OK)


def extract_price(
    raw_content: str,
    source_type: SourceType,
    default_currency: str = "USD",
) -> ExtractedPrice:
    """Route content to the extractor selected by ``source_type``."""
    if source_type == SourceType.JSONLD:
        return extract_jsonld_price(raw_content, default_currency)
    if source_type == SourceType.DOM:
        return extract_dom_price(raw_content, default_currency)
    if source_type == SourceType.JSON_API:
        return extract_json_api_price(raw_content, default_currency)
    return _error(
        f"Unsupported source type: {source_type}",
        _normalize_currency(default_currency, "USD"),
    )
