"""Google Chat platform adapter.

The adapter uses a service-account key for runtime bot calls (`chat.bot`).
Space provisioning is handled separately with DWD user credentials in the
shared auth helper so the two paths do not get conflated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    HAS_GOOGLE_API = True
except ImportError:  # pragma: no cover - dependency gating
    build = None
    HttpError = Exception
    HAS_GOOGLE_API = False

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.platforms import google_chat_auth

logger = logging.getLogger(__name__)


def check_google_chat_requirements() -> bool:
    """Return True when Google Chat runtime dependencies are available."""
    if not HAS_GOOGLE_API:
        logger.warning("[Google Chat] google-api-python-client not installed")
        return False
    
    # Check for project ID
    if not (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
    ):
        logger.warning("[Google Chat] No GCP project ID set")
        return False
    
    # Check for credentials (either service account key OR ADC + DWD)
    from gateway.platforms.google_chat_auth import (
        resolve_google_chat_service_account_file,
        _get_adc_credentials,
    )
    
    has_sa_key = resolve_google_chat_service_account_file() is not None
    has_adc = _get_adc_credentials() is not None
    has_dwd_subject = bool(os.getenv("GOOGLE_CHAT_DWD_SUBJECT"))
    
    if has_sa_key:
        return True
    if has_adc and has_dwd_subject:
        return True
    
    logger.warning(
        "[Google Chat] No valid credentials. Need either:\n"
        "  1. GOOGLE_APPLICATION_CREDENTIALS pointing to service account JSON, OR\n"
        "  2. ADC credentials (gcloud auth application-default login) + GOOGLE_CHAT_DWD_SUBJECT"
    )
    return False


def normalize_google_chat_webhook_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize webhook payload fields into a stable shape for dispatch."""
    message_data = payload.get("message") or {}
    sender = payload.get("user") or {}
    space = payload.get("space") or {}

    space_name = str(space.get("name") or "")
    space_short_id = space_name.split("/", 1)[-1] if space_name.startswith("spaces/") else space_name
    space_type = str(space.get("type") or "SPACE").upper()
    thread = message_data.get("thread") or {}

    return {
        "text": str(message_data.get("text") or ""),
        "sender_email": str(sender.get("email") or sender.get("name") or ""),
        "sender_name": str(sender.get("displayName") or sender.get("name") or "Unknown"),
        "space_id": space_name,
        "space_short_id": space_short_id,
        "space_type": space_type,
        "message_id": str(message_data.get("name") or ""),
        "thread_id": thread.get("name"),
        "is_dm": space_type == "DM",
    }


class GoogleChatAdapter(BasePlatformAdapter):
    """Google Chat bot adapter backed by app authentication."""

    MAX_MESSAGE_LENGTH = 39000

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.GOOGLE_CHAT)
        self._chat_service = None
        self._bot_email: Optional[str] = None
        self._webhook_server = None
        self._webhook_task = None
        self._connected = False
        self._project_id = (
            os.getenv("GOOGLE_CLOUD_PROJECT")
            or os.getenv("GCP_PROJECT")
            or os.getenv("GCLOUD_PROJECT")
        )
        self._user_cache: Dict[str, Dict[str, Any]] = {}

    async def connect(self) -> bool:
        """Initialize the Chat API client and optional webhook server."""
        if not HAS_GOOGLE_API:
            logger.error("[Google Chat] google-api-python-client not installed")
            return False
        if not self._project_id:
            logger.error("[Google Chat] No GCP project ID set")
            return False
        if not google_chat_service_account_available() and not (
            google_chat_auth._get_adc_credentials() is not None and os.getenv("GOOGLE_CHAT_DWD_SUBJECT")
        ):
            logger.error("[Google Chat] No valid credentials. Need either SA JSON key or ADC + GOOGLE_CHAT_DWD_SUBJECT")
            return False

        try:
            credentials = build_google_chat_app_credentials()
            self._chat_service = build("chat", "v1", credentials=credentials)
            self._bot_email = getattr(credentials, "service_account_email", None)
            self._connected = True

            await self._start_webhook_server()
            logger.info("[Google Chat] Connected as %s", self._bot_email or "service account")
            return True
        except Exception:
            logger.exception("[Google Chat] Connection failed")
            self._chat_service = None
            self._connected = False
            return False

    async def _start_webhook_server(self) -> None:
        """Start a local webhook server when the optional web stack is installed."""
        try:
            from fastapi import FastAPI, HTTPException, Request
            from fastapi.responses import JSONResponse
            import uvicorn
        except ImportError:
            logger.debug("[Google Chat] fastapi/uvicorn not installed; webhook server disabled")
            return

        app = FastAPI()

        @app.post("/google-chat/webhook")
        async def webhook_handler(request: Request):
            try:
                payload = await request.json()
                await self._handle_webhook_message(payload)
                return JSONResponse(content={})
            except HTTPException:
                raise
            except Exception as exc:  # pragma: no cover - defensive logging path
                logger.exception("[Google Chat] Webhook error")
                raise HTTPException(status_code=500, detail=str(exc)) from exc

        config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning")
        self._webhook_server = uvicorn.Server(config)
        self._webhook_task = asyncio.create_task(self._webhook_server.serve())

    async def _handle_webhook_message(self, payload: Dict[str, Any]) -> None:
        """Translate a Google Chat webhook payload into a Hermes message event."""
        try:
            normalized = normalize_google_chat_webhook_payload(payload)
            if not normalized["text"]:
                logger.debug("[Google Chat] Skipping empty webhook payload")
                return

            source = self.build_source(
                chat_id=normalized["space_short_id"],
                chat_name=normalized["space_id"],
                chat_type="dm" if normalized["is_dm"] else "group",
                user_id=normalized["sender_email"],
                user_name=normalized["sender_name"],
                thread_id=normalized["thread_id"],
                chat_id_alt=normalized["space_id"],
            )

            event = MessageEvent(
                source=source,
                message_type=MessageType.TEXT,
                text=normalized["text"],
                timestamp=datetime.utcnow().isoformat(),
            )
            await self.handle_message(event)
        except Exception:
            logger.exception("[Google Chat] Error handling webhook message")

    async def disconnect(self) -> None:
        """Tear down the webhook server and drop the Chat client."""
        if self._webhook_task:
            self._webhook_task.cancel()
            try:
                await self._webhook_task
            except asyncio.CancelledError:
                pass
            self._webhook_task = None

        if self._webhook_server:
            await self._webhook_server.shutdown()
            self._webhook_server = None

        self._chat_service = None
        self._bot_email = None
        self._connected = False
        logger.info("[Google Chat] Disconnected")

    async def send(self, chat_id: str, text: str, **kwargs) -> SendResult:
        """Send a text message to a Google Chat space."""
        if not self._chat_service or not self._connected:
            return SendResult(success=False, error="Not connected")

        try:
            payload = {"text": text}
            thread_id = kwargs.get("thread_id")
            if thread_id:
                payload["thread"] = {"name": thread_id}

            response = self._chat_service.spaces().messages().create(
                parent=f"spaces/{chat_id}",
                body=payload,
            ).execute()
            return SendResult(success=True, message_id=response.get("name", ""), raw_response=response)
        except HttpError as exc:
            logger.error("[Google Chat] Send failed: %s", exc)
            return SendResult(success=False, error=str(exc))
        except Exception as exc:
            logger.exception("[Google Chat] Unexpected send error")
            return SendResult(success=False, error=str(exc))

    async def send_typing(self, chat_id: str) -> None:
        """Google Chat does not expose a typing indicator API."""
        return None

    async def get_chat_info(self, chat_id: str) -> dict:
        """Fetch Google Chat space metadata."""
        if not self._chat_service or not self._connected:
            return {"name": chat_id, "type": "unknown", "chat_id": chat_id}

        try:
            response = self._chat_service.spaces().get(name=f"spaces/{chat_id}").execute()
            return {
                "name": response.get("displayName", chat_id),
                "type": str(response.get("type", "SPACE")).lower(),
                "chat_id": chat_id,
                "raw_response": response,
            }
        except Exception as exc:
            logger.warning("[Google Chat] Could not get chat info for %s: %s", chat_id, exc)
            return {"name": chat_id, "type": "unknown", "chat_id": chat_id}

    async def send_image(self, chat_id: str, image_url: str, caption: str = None) -> SendResult:
        """Send an image reference by posting the URL in text."""
        if not self._chat_service or not self._connected:
            return SendResult(success=False, error="Not connected")

        try:
            text = caption or "Image"
            if image_url:
                text = f"{text}\n\n![Image]({image_url})" if text else f"![Image]({image_url})"
            response = self._chat_service.spaces().messages().create(
                parent=f"spaces/{chat_id}",
                body={"text": text},
            ).execute()
            return SendResult(success=True, message_id=response.get("name", ""), raw_response=response)
        except HttpError as exc:
            logger.error("[Google Chat] Image send failed: %s", exc)
            return SendResult(success=False, error=str(exc))
        except Exception as exc:
            logger.exception("[Google Chat] Unexpected image send error")
            return SendResult(success=False, error=str(exc))


GOOGLE_CHAT_HINT = (
    "You are on Google Chat. Messages support basic Markdown formatting. "
    "Images can be shared via URLs. Thread replies are supported. "
    f"Runtime bot auth uses the {GOOGLE_CHAT_BOT_SCOPE} scope."
)
