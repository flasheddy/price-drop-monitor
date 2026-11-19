"""Deterministic offline HTML fixture sanitization using selectolax.lexbor.

Preserves ``<script type="application/ld+json">`` blocks so ``schema.org/Product``
metadata survives sanitization while removing executable scripts, styles, SVGs,
and credential/tracking attributes.
"""

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

_DECOMPOSE_TAGS: tuple[str, ...] = ("style", "noscript", "iframe", "svg")

_TRACKING_ATTR_PREFIXES: tuple[str, ...] = (
    "on",
    "data-analytics",
    "data-gtm",
    "data-tracking",
)

_TRACKING_PARAM_PREFIXES: tuple[str, ...] = ("utm_", "ga_", "gtm_", "nad_")
_TRACKING_PARAM_EXACT: frozenset[str] = frozenset(
    {"fbclid", "gclid", "sid", "aid", "label", "srpvid", "srepoch", "from"}
)

_SUSPICIOUS_ATTR_SUBSTRINGS: tuple[str, ...] = (
    "mailto-token",
    "mailto-vector",
    "csrf",
    "nonce",
    "api-key",
    "apikey",
    "client-secret",
    "auth-token",
    "session-token",
)

_CREDENTIAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.]{16,}\b", re.IGNORECASE), "Bearer [SYNTHETIC_TOKEN]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "ghp_SYNTHETIC_GITHUB_TOKEN"),
    (re.compile(r"\bAIza[0-9A-Za-z-_]{30,40}\b"), "AIzaSySYNTHETIC_GOOGLE_API_KEY"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIAIOSFODNN7EXAMPLE"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "sk-SYNTHETIC_API_KEY"),
    (
        re.compile(
            r"""(?i)\b(api[-_]?key|access[-_]?token|auth[-_]?token|client[-_]?secret|password|passwd|secret)\b(\s*[:=]\s*["']?)([^"'\s]{8,})(["']?)"""
        ),
        r"\g<1>\g<2>[SYNTHETIC_CREDENTIAL]\g<4>",
    ),
)


def _is_tracking_or_suspicious_attr(attr: str) -> bool:
    lower = attr.lower()
    if lower.startswith(_TRACKING_ATTR_PREFIXES):
        return True
    if any(sub in lower for sub in _SUSPICIOUS_ATTR_SUBSTRINGS):
        return True
    return lower.endswith(("-token", "_token", "-key", "_key", "-secret", "_secret"))


def _is_tracking_param(param_key: str) -> bool:
    lower = param_key.lower()
    return lower.startswith(_TRACKING_PARAM_PREFIXES) or lower in _TRACKING_PARAM_EXACT


def _is_jsonld_script(script_type: str | None) -> bool:
    if script_type is None:
        return False
    mime = script_type.split(";", 1)[0].strip().lower()
    return mime == "application/ld+json"


def redact_credentials(text: str) -> str:
    """Redact credential-shaped tokens with synthetic placeholders."""
    redacted = text
    for pattern, replacement in _CREDENTIAL_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def strip_url_tracking(url: str) -> str:
    """Strip tracking query parameters from an href URL."""
    if not url or "?" not in url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url

    query_tuples = parse_qsl(parts.query, keep_blank_values=True)
    filtered = [(k, v) for k, v in query_tuples if not _is_tracking_param(k)]
    new_query = urlencode(filtered)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def _normalize_line_whitespace(text: str) -> str:
    """Trim line-end spaces/tabs, collapse blank runs, and end with one newline."""
    lines = text.splitlines()
    cleaned = "\n".join(line.rstrip(" \t") for line in lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return f"{cleaned}\n" if cleaned else ""


def sanitize_html(html: str) -> str:
    """Sanitize raw HTML fixture while preserving ``application/ld+json`` blocks."""
    tree = HTMLParser(html)
    root = tree.root
    start_node = root.parent if root is not None and root.parent is not None else root
    if start_node is not None:
        for node in list(start_node.traverse(include_text=True)):
            if node.is_comment_node:
                node.decompose()

    for node in tree.css("script"):
        if not _is_jsonld_script(node.attributes.get("type")):
            node.decompose()

    for tag in _DECOMPOSE_TAGS:
        for node in tree.css(tag):
            node.decompose()

    for node in tree.css("*"):
        to_delete = [attr for attr in node.attributes if _is_tracking_or_suspicious_attr(attr)]
        for attr in to_delete:
            del node.attrs[attr]

        if "href" in node.attributes:
            href_val = node.attributes.get("href")
            if href_val:
                node.attrs["href"] = strip_url_tracking(href_val)

    output = tree.html or ""
    return _normalize_line_whitespace(redact_credentials(output))


def sanitize_file(path: Path) -> None:
    """Sanitize an HTML fixture file in-place idempotently."""
    content = path.read_text(encoding="utf-8")
    sanitized = sanitize_html(content)
    path.write_text(sanitized, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: sanitize an offline HTML fixture file in-place or via stdin/stdout."""
    parser = argparse.ArgumentParser(
        description=(
            "Sanitize offline HTML fixtures while preserving application/ld+json metadata."
        ),
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Path to HTML fixture to sanitize in-place. If omitted or '-', reads stdin/stdout.",
    )
    args = parser.parse_args(argv)

    if args.file and args.file != "-":
        path = Path(args.file)
        if not path.is_file():
            sys.stderr.write(f"Error: file not found: {args.file}\n")
            return 1
        sanitize_file(path)
        return 0

    input_data = sys.stdin.read()
    sanitized = sanitize_html(input_data)
    sys.stdout.write(sanitized)
    return 0


if __name__ == "__main__":
    sys.exit(main())
