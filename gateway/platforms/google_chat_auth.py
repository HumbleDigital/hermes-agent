"""Shared Google Chat authentication helpers.

This module keeps the runtime bot credentials and the DWD provisioning
credentials intentionally separate so the adapter and smoke scripts cannot
accidentally treat them as interchangeable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

try:
    from google.oauth2 import service_account
    HAS_GOOGLE_AUTH = True
except ImportError:  # pragma: no cover - dependency gating
    service_account = None
    HAS_GOOGLE_AUTH = False


GOOGLE_CHAT_BOT_SCOPE = "https://www.googleapis.com/auth/chat.bot"
GOOGLE_CHAT_SPACES_CREATE_SCOPE = "https://www.googleapis.com/auth/chat.spaces.create"


def resolve_google_chat_service_account_file() -> Optional[Path]:
    """Return the configured service-account key path, if present."""
    value = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.exists() else None


def google_chat_service_account_available() -> bool:
    """Return True when a service-account key file is available."""
    return HAS_GOOGLE_AUTH and resolve_google_chat_service_account_file() is not None


def _build_credentials(scopes: Iterable[str], subject: str | None = None):
    if not HAS_GOOGLE_AUTH:
        raise RuntimeError("google-auth is not installed")

    key_path = resolve_google_chat_service_account_file()
    if key_path is None:
        raise FileNotFoundError(
            "GOOGLE_APPLICATION_CREDENTIALS must point to a service-account JSON key"
        )

    kwargs = {"scopes": list(scopes)}
    if subject:
        kwargs["subject"] = subject
    return service_account.Credentials.from_service_account_file(str(key_path), **kwargs)


def build_google_chat_app_credentials():
    """Build runtime bot credentials with the chat.bot scope."""
    return _build_credentials([GOOGLE_CHAT_BOT_SCOPE])


def build_google_chat_dwd_user_credentials(subject: str):
    """Build DWD provisioning credentials for the delegated user."""
    if not subject:
        raise ValueError("subject is required for DWD provisioning credentials")
    return _build_credentials([GOOGLE_CHAT_SPACES_CREATE_SCOPE], subject=subject)
