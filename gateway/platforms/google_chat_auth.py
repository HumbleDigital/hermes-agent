"""Shared Google Chat authentication helpers.

This module keeps the runtime bot credentials and the DWD provisioning
credentials intentionally separate so the adapter and smoke scripts cannot
accidentally treat them as interchangeable.

Supports two auth paths:
1. Service account JSON key file (GOOGLE_APPLICATION_CREDENTIALS points to JSON)
2. ADC user credentials with DWD impersonation (for local dev and GCE with IAM)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

try:
    from google.oauth2 import service_account
    from google.oauth2 import credentials as oauth2_credentials
    from google.auth import impersonated_credentials
    from google.auth import default as adc_default
    HAS_GOOGLE_AUTH=True
except ImportError:  # pragma: no cover - dependency gating
    service_account = None
    oauth2_credentials = None
    impersonated_credentials = None
    adc_default = None
    HAS_GOOGLE_AUTH=False


GOOGLE_CHAT_BOT_SCOPE = "https://www.googleapis.com/auth/chat.bot"
GOOGLE_CHAT_SPACES_CREATE_SCOPE = "https://www.googleapis.com/auth/chat.spaces.create"


def resolve_google_chat_service_account_file() -> Optional[Path]:
    """Return the configured service-account key path, if present and valid.
    
    Only returns a path if the file looks like a service account JSON key
    (has 'client_email' and 'type' = 'service_account'). ADC user credentials
    are NOT service account keys and should return None.
    """
    value = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.exists():
        return None
    
    # Check if it's actually a service account key (not ADC user credentials)
    try:
        import json
        with open(path) as f:
            data = json.load(f)
        if data.get("type") == "service_account" and "client_email" in data:
            return path
        # It's ADC user credentials or some other format
        return None
    except Exception:
        return None


def google_chat_service_account_available() -> bool:
    """Return True when a service-account key file is available."""
    return HAS_GOOGLE_AUTH and resolve_google_chat_service_account_file() is not None


def _get_adc_credentials() -> Optional[Any]:
    """Get ADC credentials if available (user credentials from gcloud auth)."""
    if not HAS_GOOGLE_AUTH:
        return None
    try:
        creds, _ = adc_default()
        return creds
    except Exception:
        return None


def _build_credentials(scopes: Iterable[str], subject: str | None = None):
    """Build credentials for Google Chat API calls.
    
    Supports two paths:
    1. Service account JSON key file (preferred for production)
    2. ADC user credentials with DWD impersonation (for local dev)
    """
    if not HAS_GOOGLE_AUTH:
        raise RuntimeError("google-auth is not installed")

    # Path 1: Service account JSON key file
    key_path = resolve_google_chat_service_account_file()
    if key_path is not None:
        kwargs = {"scopes": list(scopes)}
        if subject:
            kwargs["subject"] = subject
        return service_account.Credentials.from_service_account_file(str(key_path), **kwargs)

    # Path 2: ADC user credentials with DWD impersonation
    adc_creds = _get_adc_credentials()
    if adc_creds is not None:
        # Read DWD subject from env var if not provided
        if not subject:
            subject = os.getenv("GOOGLE_CHAT_DWD_SUBJECT")
        if not subject:
            raise ValueError(
                "DWD subject (GOOGLE_CHAT_DWD_SUBJECT) is required when using ADC credentials. "
                "Set it to the Workspace user email to impersonate (e.g., user@domain.com)."
            )
        # Use impersonated credentials to act as the DWD subject
        return impersonated_credentials.Credentials(
            source_credentials=adc_creds,
            target_principal=subject,
            target_scopes=list(scopes),
        )

    # No credentials available
    raise FileNotFoundError(
        "No Google credentials found. Either:\\n"
        "1. Set GOOGLE_APPLICATION_CREDENTIALS to a service account JSON key file, OR\\n"
        "2. Run 'gcloud auth application-default login' and set GOOGLE_CHAT_DWD_SUBJECT"
    )


def build_google_chat_app_credentials():
    """Build runtime bot credentials with the chat.bot scope.
    
    For production: uses service account JSON key with chat.bot scope.
    For local dev: uses ADC + DWD impersonation (requires GOOGLE_CHAT_DWD_SUBJECT).
    """
    return _build_credentials([GOOGLE_CHAT_BOT_SCOPE])


def build_google_chat_dwd_user_credentials(subject: str):
    """Build DWD provisioning credentials for the delegated user.
    
    For production: uses service account JSON key with DWD subject.
    For local dev: uses ADC + DWD impersonation.
    """
    if not subject:
        raise ValueError("subject is required for DWD provisioning credentials")
    return _build_credentials([GOOGLE_CHAT_SPACES_CREATE_SCOPE], subject=subject)
