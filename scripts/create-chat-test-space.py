#!/usr/bin/env python3
"""Create a Google Chat space for Phase 1 local testing.

This script uses DWD user credentials to set up a named space that the
delegated human owns and can see. The runtime bot path remains separate and
uses app credentials.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from uuid import uuid4

from gateway.platforms.google_chat_auth import (
    build_google_chat_dwd_user_credentials,
    google_chat_service_account_available,
)

try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    print("ERROR: google-api-python-client not installed")
    print("Install with: pip install google-api-python-client")
    sys.exit(1)


def check_credentials() -> bool:
    """Validate the delegated-user setup prerequisites."""
    if not google_chat_service_account_available():
        print("ERROR: GOOGLE_APPLICATION_CREDENTIALS must point to a service-account JSON key")
        print("Set it before running this script.")
        return False

    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or os.getenv("GCLOUD_PROJECT")
    if not project:
        print("ERROR: GOOGLE_CLOUD_PROJECT not set")
        print("Run: export GOOGLE_CLOUD_PROJECT=<your-project-id>")
        return False

    subject = os.getenv("GOOGLE_CHAT_DWD_SUBJECT")
    if not subject:
        print("ERROR: GOOGLE_CHAT_DWD_SUBJECT not set")
        print("Run: export GOOGLE_CHAT_DWD_SUBJECT=<delegated-user-email>")
        return False

    return True


def create_test_space() -> str | None:
    """Create a named Chat space with the delegated user as the owner/member."""
    subject = os.getenv("GOOGLE_CHAT_DWD_SUBJECT")
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or os.getenv("GCLOUD_PROJECT")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    space_name = f"Phase1-Test-{timestamp}"

    print(f"Creating test space: {space_name}")
    print(f"Delegated user: {subject}")
    print(f"Project: {project}")
    print()

    try:
        credentials = build_google_chat_dwd_user_credentials(subject)
        chat_service = build("chat", "v1", credentials=credentials)

        response = chat_service.spaces().setup(
            body={
                "space": {
                    "spaceType": "SPACE",
                    "displayName": space_name,
                },
                "requestId": str(uuid4()),
            }
        ).execute()

        space_name_value = response.get("name", "")
        space_short_id = space_name_value.split("/", 1)[-1] if space_name_value.startswith("spaces/") else space_name_value

        print("✓ Space created successfully!")
        print()
        print("Environment values for the smoke runner:")
        print(f"GOOGLE_CHAT_SPACE_ID={space_short_id}")
        print(f"GOOGLE_CHAT_SPACE_NAME={space_name}")
        print()
        print("Space identifier for API calls:")
        print(f"  {space_name_value}")
        return space_short_id
    except HttpError as exc:
        print(f"ERROR: Google Chat API error: {exc}")
        status = getattr(exc, "status_code", None) or getattr(getattr(exc, "resp", None), "status", None)
        if status == 403:
            print()
            print("Permission denied. Check:")
            print("  1. Chat API is enabled in your Google Workspace")
            print("  2. The service account has DWD configured for the chat.spaces.create scope")
            print("  3. GOOGLE_CHAT_DWD_SUBJECT is a valid delegated user")
        elif status == 404:
            print()
            print("404 error. Check:")
            print("  1. Chat API is enabled: https://console.cloud.google.com/apis/library/chat.googleapis.com")
            print("  2. The delegated user can create Chat spaces")
        return None
    except Exception as exc:
        print(f"ERROR: Unexpected error: {exc}")
        return None


if __name__ == "__main__":
    print("=" * 60)
    print("Google Chat Test Space Creator")
    print("=" * 60)
    print()

    if not check_credentials():
        sys.exit(1)

    space_id = create_test_space()
    if space_id:
        print()
        print("Next step:")
        print("  Run: python3 scripts/test-phase1-local.py")
        sys.exit(0)

    sys.exit(1)
