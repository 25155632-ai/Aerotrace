"""Configuration for the Aerotrace prototype backend.

Everything comes from the environment with safe local defaults. There are
no secrets in this stage: if an upstream ever needs a credential, it is
read here from the environment by the backend only and is never sent to
the frontend. The server binds to loopback by default (see main.py).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Aerotrace"
APP_VERSION = "0.1.0"

# Allowlisted upstream host. This is a constant, never user input, so it
# cannot be turned into an SSRF vector by a request (see docs/SECURITY.md).
OPENSKY_BASE_URL = "https://opensky-network.org"
ADSB_LOL_BASE_URL = "https://api.adsb.lol"
AIRPLANES_LIVE_BASE_URL = "https://api.airplanes.live"


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    frontend_origin: str = "http://127.0.0.1:5173"
    database_path: Path = Path(__file__).resolve().parent.parent / "data" / "spoofsky.db"
    max_page_size: int = 100
    alert_dispatch_interval_seconds: float = 1.0
    alert_max_attempts: int = 5
    redis_url: str | None = None
    enabled_adsb_provider: str = "opensky"
    adsb_lol_enabled: bool = False
    airplanes_live_enabled: bool = False
    provider_timeout_seconds: float = 10.0
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    twilio_to_numbers: tuple[str, ...] = ()
    sendgrid_api_key: str | None = None
    sendgrid_from_email: str | None = None
    sendgrid_to_emails: tuple[str, ...] = ()
    isolation_forest_artifact: Path | None = None
    xgboost_artifact: Path | None = None
    lstm_artifact: Path | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        def _csv_env(name: str) -> tuple[str, ...]:
            return tuple(value.strip() for value in os.environ.get(name, "").split(",") if value.strip())

        def _path_env(name: str) -> Path | None:
            value = os.environ.get(name)
            return Path(value) if value else None

        return cls(
            host=os.environ.get("SPOOFSKY_HOST", "127.0.0.1"),
            port=int(os.environ.get("SPOOFSKY_PORT", "8000")),
            frontend_origin=os.environ.get(
                "SPOOFSKY_FRONTEND_ORIGIN", "http://127.0.0.1:5173"
            ),
            database_path=Path(
                os.environ.get(
                    "SPOOFSKY_DATABASE_PATH",
                    str(Path(__file__).resolve().parent.parent / "data" / "spoofsky.db"),
                )
            ),
            max_page_size=int(os.environ.get("SPOOFSKY_MAX_PAGE_SIZE", "100")),
            alert_dispatch_interval_seconds=float(os.environ.get("SPOOFSKY_ALERT_DISPATCH_INTERVAL", "1")),
            alert_max_attempts=int(os.environ.get("SPOOFSKY_ALERT_MAX_ATTEMPTS", "5")),
            redis_url=os.environ.get("SPOOFSKY_REDIS_URL") or None,
            enabled_adsb_provider=os.environ.get("SPOOFSKY_ADSB_PROVIDER", "opensky"),
            adsb_lol_enabled=os.environ.get("SPOOFSKY_ADSB_LOL_ENABLED", "0") == "1",
            airplanes_live_enabled=os.environ.get("SPOOFSKY_AIRPLANES_LIVE_ENABLED", "0") == "1",
            provider_timeout_seconds=float(os.environ.get("SPOOFSKY_PROVIDER_TIMEOUT", "10")),
            twilio_account_sid=os.environ.get("TWILIO_ACCOUNT_SID") or None,
            twilio_auth_token=os.environ.get("TWILIO_AUTH_TOKEN") or None,
            twilio_from_number=os.environ.get("TWILIO_FROM_NUMBER") or None,
            twilio_to_numbers=_csv_env("TWILIO_TO_NUMBERS"),
            sendgrid_api_key=os.environ.get("SENDGRID_API_KEY") or None,
            sendgrid_from_email=os.environ.get("SENDGRID_FROM_EMAIL") or None,
            sendgrid_to_emails=_csv_env("SENDGRID_TO_EMAILS"),
            isolation_forest_artifact=_path_env("SPOOFSKY_ISOLATION_FOREST_ARTIFACT"),
            xgboost_artifact=_path_env("SPOOFSKY_XGBOOST_ARTIFACT"),
            lstm_artifact=_path_env("SPOOFSKY_LSTM_ARTIFACT"),
        )
