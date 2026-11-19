"""Strict threshold evaluation, consolidated formatting, and injectable notifiers."""

import contextlib
import smtplib
from collections.abc import Callable, Sequence
from email.message import EmailMessage
from typing import Any

import httpx

from .models import CheckStatus, PriceAlert, PriceCheckResult


def evaluate_alerts(results: Sequence[PriceCheckResult]) -> list[PriceAlert]:
    """Return alerts only for successful checks strictly below the target price."""
    alerts: list[PriceAlert] = []
    for result in results:
        current_price = result.current_price
        if (
            result.status != CheckStatus.OK
            or current_price is None
            or current_price >= result.target_price
        ):
            continue
        savings = (
            round(max(0.0, result.old_price - current_price), 2)
            if result.old_price is not None
            else None
        )
        alerts.append(
            PriceAlert(
                product_id=result.product_id,
                name=result.name,
                category=result.category,
                url=result.url,
                old_price=result.old_price,
                new_price=current_price,
                target_price=result.target_price,
                currency=result.currency,
                savings=savings,
            )
        )
    return alerts


def _money(amount: float, currency: str) -> str:
    return f"{currency} {amount:.2f}"


def format_consolidated_alert(alerts: Sequence[PriceAlert]) -> str:
    """Build one plain-text report listing every triggered deal."""
    if not alerts:
        return "No price drops detected."

    lines = [f"Price Drop Alert: {len(alerts)} item(s) below target", ""]
    for index, alert in enumerate(alerts, start=1):
        old_price = (
            _money(alert.old_price, alert.currency) if alert.old_price is not None else "n/a"
        )
        savings = _money(alert.savings, alert.currency) if alert.savings is not None else "n/a"
        lines.extend(
            [
                f"{index}. {alert.name} ({alert.product_id})",
                f"   Old price: {old_price}",
                f"   New price: {_money(alert.new_price, alert.currency)}",
                f"   Target price: {_money(alert.target_price, alert.currency)}",
                f"   Savings: {savings}",
                f"   URL: {alert.url}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


class EmailNotifier:
    """Send the consolidated alert through injectable SMTP with STARTTLS."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str | None,
        smtp_app_password: str | None,
        to_email: str,
        smtp_factory: Callable[[str, int], Any] = smtplib.SMTP,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_app_password = smtp_app_password
        self.to_email = to_email
        self.smtp_factory = smtp_factory

    def send(self, alerts: Sequence[PriceAlert]) -> None:
        """Send one consolidated email for the supplied alerts."""
        if not alerts:
            return
        message = EmailMessage()
        message["Subject"] = f"Price Drop Alert: {len(alerts)} item(s) below target"
        message["From"] = self.smtp_user or "price-drop-monitor@localhost"
        message["To"] = self.to_email
        message.set_content(format_consolidated_alert(alerts))

        server = self.smtp_factory(self.smtp_host, self.smtp_port)
        try:
            server.starttls()
            if self.smtp_user and self.smtp_app_password:
                server.login(self.smtp_user, self.smtp_app_password)
            server.send_message(message)
        finally:
            quit_method = getattr(server, "quit", None)
            if callable(quit_method):
                with contextlib.suppress(smtplib.SMTPException, OSError):
                    quit_method()


class WebhookNotifier:
    """POST the consolidated alert payload to ntfy, Discord, or a custom webhook."""

    def __init__(self, webhook_url: str, client: httpx.Client | None = None) -> None:
        self.webhook_url = webhook_url
        self._client = client

    def send(self, alerts: Sequence[PriceAlert]) -> None:
        """Send one consolidated JSON payload for the supplied alerts."""
        if not alerts:
            return
        message = format_consolidated_alert(alerts)
        payload = {
            "text": message,
            "content": message,
            "alert_count": len(alerts),
        }
        if self._client is None:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(self.webhook_url, json=payload)
                response.raise_for_status()
            return
        response = self._client.post(self.webhook_url, json=payload)
        response.raise_for_status()
