"""Optional Twilio and SendGrid notification adapters.

Secrets are read only from backend settings. No provider call is made when
configuration is incomplete; the delivery history records ``disabled`` so
the UI can distinguish it from a successful send or a transient failure.
"""

from __future__ import annotations

import json
import re

import httpx

from .store import get_alert_rule, record_delivery_attempt


def provider_status(settings) -> dict[str, dict]:
    sms_valid = bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_from_number
        and settings.twilio_to_numbers
        and all(re.fullmatch(r"\+[1-9][0-9]{6,14}", value) for value in (settings.twilio_from_number, *settings.twilio_to_numbers))
    )
    email_valid = bool(
        settings.sendgrid_api_key
        and settings.sendgrid_from_email
        and settings.sendgrid_to_emails
        and all(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) for value in (settings.sendgrid_from_email, *settings.sendgrid_to_emails))
    )
    return {
        "in_app": {"configured": True, "status": "available"},
        "sms": {
            "configured": sms_valid,
            "status": "configured" if sms_valid else "unconfigured_or_invalid",
        },
        "email": {
            "configured": email_valid,
            "status": "configured" if email_valid else "unconfigured_or_invalid",
        },
    }


def _message(alert: dict) -> str:
    return f"Aerotrace alert: {alert['title']}. {alert['summary']} Provenance: {alert['provenance']}."


def _send_sms(settings, body: str) -> str:
    with httpx.Client(timeout=10.0) as client:
        responses = []
        for destination in settings.twilio_to_numbers:
            response = client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
                data={"From": settings.twilio_from_number, "To": destination, "Body": body[:1600]},
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            )
            response.raise_for_status()
            responses.append(str(response.status_code))
        return ",".join(responses)


def _send_email(settings, alert: dict, body: str) -> str:
    payload = {
        "personalizations": [{"to": [{"email": address} for address in settings.sendgrid_to_emails]}],
        "from": {"email": settings.sendgrid_from_email},
        "subject": f"Aerotrace alert: {alert['title']}",
        "content": [{"type": "text/plain", "value": body[:10000]}],
    }
    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {settings.sendgrid_api_key}", "Content-Type": "application/json"},
            content=json.dumps(payload),
        )
        response.raise_for_status()
        return str(response.status_code)


def deliver_alert(connection, alert: dict, settings) -> None:
    rule = get_alert_rule(connection, int(alert["rule_id"]))
    channels = rule["channels"] if rule else ["in_app"]
    body = _message(alert)
    for channel in channels:
        if channel == "in_app":
            record_delivery_attempt(connection, alert_id=int(alert["id"]), channel=channel, status="sent", provider_response="local-queue")
        elif channel == "sms":
            configured = provider_status(settings)["sms"]["configured"]
            if not configured:
                record_delivery_attempt(connection, alert_id=int(alert["id"]), channel=channel, status="disabled", error="Twilio credentials or recipients are not configured")
                continue
            try:
                result = _send_sms(settings, body)
                record_delivery_attempt(connection, alert_id=int(alert["id"]), channel=channel, status="sent", provider_response=result)
            except (httpx.HTTPError, ValueError) as exc:
                record_delivery_attempt(connection, alert_id=int(alert["id"]), channel=channel, status="failed", error=type(exc).__name__)
        elif channel == "email":
            configured = provider_status(settings)["email"]["configured"]
            if not configured:
                record_delivery_attempt(connection, alert_id=int(alert["id"]), channel=channel, status="disabled", error="SendGrid credentials or recipients are not configured")
                continue
            try:
                result = _send_email(settings, alert, body)
                record_delivery_attempt(connection, alert_id=int(alert["id"]), channel=channel, status="sent", provider_response=result)
            except (httpx.HTTPError, ValueError) as exc:
                record_delivery_attempt(connection, alert_id=int(alert["id"]), channel=channel, status="failed", error=type(exc).__name__)
