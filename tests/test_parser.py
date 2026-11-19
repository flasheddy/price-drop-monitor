"""Offline fixture smoke tests for price-drop-monitor scaffold."""

from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

from scripts.sanitize_fixture import sanitize_html

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


def test_sanitize_html_preserves_jsonld_and_strips_tracking() -> None:
    """Verify sanitizer preserves JSON-LD product data while removing regular scripts/tokens."""
    raw = (
        "<html><head>"
        '<script type="application/ld+json">{"@type":"Product","price":"10.00"}</script>'
        "<script>alert('xss')</script>"
        "</head><body>"
        '<a href="https://example.com/item?utm_source=test" data-auth-token="dummy">Go</a>'
        "</body></html>"
    )
    cleaned = sanitize_html(raw)
    assert 'type="application/ld+json"' in cleaned
    assert '"@type":"Product"' in cleaned
    assert "alert('xss')" not in cleaned
    assert "utm_source" not in cleaned
    assert "data-auth-token" not in cleaned
