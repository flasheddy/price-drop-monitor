"""Offline parser and fail-closed model tests for price-drop-monitor."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from selectolax.lexbor import LexborHTMLParser

from price_drop_monitor.extractors import (
    extract_dom_price,
    extract_json_api_price,
    extract_jsonld_price,
)
from price_drop_monitor.models import CheckStatus, PriceCheckResult

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

EXPECTED_FIXTURES = (
    "watchlist_targets.csv",
    "cycling_garmin_edge.html",
    "cycling_gp5000_tire.html",
    "hardware_nvme_ssd.html",
    "hardware_missing_price.html",
    "software_deal_api.json",
)


def test_offline_fixtures_present() -> None:
    """Verify all sanitized offline fixtures exist and are non-empty."""
    for name in EXPECTED_FIXTURES:
        path = FIXTURES_DIR / name
        assert path.is_file(), f"Missing fixture: {path}"
        assert path.stat().st_size > 0, f"Empty fixture: {path}"


def test_html_fixtures_contain_expected_nodes() -> None:
    """Verify HTML fixtures retain JSON-LD blocks or DOM price nodes."""
    edge_html = (FIXTURES_DIR / "cycling_garmin_edge.html").read_text(encoding="utf-8")
    edge_tree = LexborHTMLParser(edge_html)
    assert edge_tree.css_first('script[type="application/ld+json"]') is not None

    tire_html = (FIXTURES_DIR / "cycling_gp5000_tire.html").read_text(encoding="utf-8")
    tire_tree = LexborHTMLParser(tire_html)
    assert tire_tree.css_first('script[type="application/ld+json"]') is not None

    ssd_html = (FIXTURES_DIR / "hardware_nvme_ssd.html").read_text(encoding="utf-8")
    ssd_tree = LexborHTMLParser(ssd_html)
    price_el = ssd_tree.css_first(".product-card .price-current")
    assert price_el is not None
    assert price_el.attributes.get("data-price") == "149.99"

    missing_html = (FIXTURES_DIR / "hardware_missing_price.html").read_text(encoding="utf-8")
    missing_tree = LexborHTMLParser(missing_html)
    assert missing_tree.css_first(".product-card .price-current") is None


def test_extract_jsonld_top_level_product() -> None:
    """Parse a top-level schema.org Product with a single offer object."""
    html = (FIXTURES_DIR / "cycling_garmin_edge.html").read_text(encoding="utf-8")

    extracted = extract_jsonld_price(html)

    assert extracted.price == pytest.approx(369.99)
    assert extracted.currency == "USD"
    assert extracted.status == CheckStatus.OK
    assert extracted.error_message is None


def test_extract_jsonld_graph_offer_list() -> None:
    """Parse a Product nested in @graph with a list of offer objects."""
    html = (FIXTURES_DIR / "cycling_gp5000_tire.html").read_text(encoding="utf-8")

    extracted = extract_jsonld_price(html)

    assert extracted.price == pytest.approx(62.50)
    assert extracted.currency == "USD"
    assert extracted.status == CheckStatus.OK
    assert extracted.error_message is None


def test_extract_jsonld_root_array_and_malformed_fallback() -> None:
    """Support root-list JSON-LD and fail closed on malformed or price-less payloads."""
    root_array_html = (
        "<html><head>"
        '<script type="application/ld+json">'
        '[{"@type": "Product", "offers": '
        '{"price": "19.99", "priceCurrency": "eur"}}]'
        "</script></head></html>"
    )
    extracted = extract_jsonld_price(root_array_html)
    assert extracted.price == pytest.approx(19.99)
    assert extracted.currency == "EUR"
    assert extracted.status == CheckStatus.OK

    malformed_html = (
        '<html><head><script type="application/ld+json">{not valid json</script></head></html>'
    )
    malformed = extract_jsonld_price(malformed_html)
    assert malformed.price is None
    assert malformed.status == CheckStatus.ERROR
    assert malformed.error_message

    price_less_html = (
        "<html><head>"
        '<script type="application/ld+json">'
        '{"@type": "Product", "name": "No Price", "offers": {"price": "n/a"}}'
        "</script></head></html>"
    )
    price_less = extract_jsonld_price(price_less_html)
    assert price_less.price is None
    assert price_less.status == CheckStatus.ERROR
    assert price_less.error_message


def test_extract_dom_price_with_data_attribute() -> None:
    """Parse the CSS data-price attribute and explicit currency node."""
    html = (FIXTURES_DIR / "hardware_nvme_ssd.html").read_text(encoding="utf-8")

    extracted = extract_dom_price(html)

    assert extracted.price == pytest.approx(149.99)
    assert extracted.currency == "USD"
    assert extracted.status == CheckStatus.OK
    assert extracted.error_message is None


def test_extract_dom_price_text_fallback() -> None:
    """Parse a text-only price when no data-price attribute exists."""
    html = (
        '<html><body><main class="product-card">'
        '<span class="price-current">$89.50</span>'
        "</main></body></html>"
    )

    extracted = extract_dom_price(html)

    assert extracted.price == pytest.approx(89.50)
    assert extracted.currency == "USD"
    assert extracted.status == CheckStatus.OK
    assert extracted.error_message is None


def test_extract_dom_missing_price_fails_closed() -> None:
    """Record an explicit extraction error instead of guessing a missing price."""
    html = (FIXTURES_DIR / "hardware_missing_price.html").read_text(encoding="utf-8")

    extracted = extract_dom_price(html)

    assert extracted.price is None
    assert extracted.status == CheckStatus.ERROR
    assert extracted.error_message
    assert extracted.error_message.strip()


def test_extract_json_api_price_ok_and_error() -> None:
    """Parse valid JSON API payloads and fail closed on invalid input."""
    payload = (FIXTURES_DIR / "software_deal_api.json").read_text(encoding="utf-8")

    extracted = extract_json_api_price(payload)
    assert extracted.price == pytest.approx(199.00)
    assert extracted.currency == "USD"
    assert extracted.status == CheckStatus.OK
    assert extracted.error_message is None

    missing_price = extract_json_api_price(json.dumps({"currency": "USD", "in_stock": True}))
    assert missing_price.price is None
    assert missing_price.status == CheckStatus.ERROR
    assert missing_price.error_message

    invalid_price = extract_json_api_price(json.dumps({"price": "not-a-number", "currency": "USD"}))
    assert invalid_price.price is None
    assert invalid_price.status == CheckStatus.ERROR
    assert invalid_price.error_message

    malformed_json = extract_json_api_price("{not valid json")
    assert malformed_json.price is None
    assert malformed_json.status == CheckStatus.ERROR
    assert malformed_json.error_message


def test_price_check_result_fail_closed_validation() -> None:
    """Reject error rows that hide or fabricate a current price or suppress the reason."""
    base = {
        "product_id": "keychron-q1-pro",
        "name": "Keychron Q1 Pro",
        "category": "tech_hardware",
        "url": "https://example.com/hardware/keychron-q1-pro",
        "source_type": "dom",
        "target_price": 180.0,
        "old_price": 199.0,
        "currency": "USD",
        "status": "error",
        "triggered_alert": False,
        "checked_at": "2026-11-19T13:00:00+00:00",
    }

    with pytest.raises(ValidationError):
        PriceCheckResult(
            **base,
            current_price=0.0,
            error_message="price element missing",
        )

    with pytest.raises(ValidationError):
        PriceCheckResult(
            **{
                **base,
                "current_price": None,
                "triggered_alert": True,
                "error_message": "price element missing",
            }
        )

    with pytest.raises(ValidationError):
        PriceCheckResult(
            **base,
            current_price=None,
            error_message="   ",
        )

    valid = PriceCheckResult(
        **base,
        current_price=None,
        error_message="price element missing",
    )
    assert valid.current_price is None
    assert valid.status == CheckStatus.ERROR
