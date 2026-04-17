"""
Google Chat platform adapter.

Uses google-api-python-client with Google Workspace DWD (Domain-Wide Delegation) for:
- Receiving messages via webhook (Google Chat incoming webhooks)
- Sending responses via Chat API
- Thread support (spaces and DMs)

Requires:
- Google Workspace with Chat API enabled
- Service account with DWD delegated for Chat API scope
- Webhook URL registered in Google Chat space or DM

Scope: https://www.googleapis.com/auth/chat.bot
"""

import asyncio
import json
import logging
import os
import time
import hmac
import hashlib
import base64
from typing import Dict, List, Optional, Any
from datetime import datetime

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    HAS_GOOGLE_API = True
except ImportError:
    HAS_GOOGLE_API = False
    service_account = None
    build = None
    HttpError = Any

import httpx

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    cache_image_from_bytes,
    safe_url_for_log,
)


logger = logging.getLogger(__name__)


def check_google_chat_requirements() -> bool:
    """Check if Google Chat dependencies and credentials are available."""
    if not HAS_GOOGLE_API:
        logger.warning("[Google Chat] google-api-python-client not installed")
        return False
    
    # Check for credentials
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    
    has_credentials = (
        (credentials_path and os.path.exists(credentials_path)) or
        os.path.exists(adc_path)
    )
    
    # Check for project ID
    has_project = bool(
        os.getenv("GOOGLE_CLOUD_PROJECT") or
        os.getenv("GCP_PROJECT") or
        os.getenv("GCLOUD_PROJECT")
    )
    
    # For webhook mode, we need WEBHOOK_SECRET for validation
    has_webhook_secret = bool(os.getenv("GOOGLE_CHAT_WEBHOOK_SECRET"))
    
    if has_credentials and has_project:
        if not has_webhook_secret:
            logger.warning("[Google Chat] GOOGLE_CHAT_WEBHOOK_SECRET not set - webhook validation disabled")
        return True
    
    return False


class GoogleChatAdapter(BasePlatformAdapter):
    """
    Google Chat bot adapter using Google Workspace DWD.
    
    Two modes of operation:
    1. Webhook mode (incoming): Receives messages via HTTP webhook from Google Chat
    2. API mode (outgoing): Sends messages via Chat API using service account
    
    Requires:
      - Service account with DWD delegated for chat.bot scope
      - GOOGLE_APPLICATION_CREDENTIALS or ADC
      - GOOGLE_CLOUD_PROJECT set
      - GOOGLE_CHAT_WEBHOOK_SECRET for webhook validation (recommended)
    
    Features:
      - DMs and space messages
      - Thread support
      - File/image attachments (via Drive links)
      - Card-based messages (future)
    """
    
    MAX_MESSAGE_LENGTH = 39000  # Google Chat limit
    
    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.GOOGLE_CHAT)
        self._chat_service = None
        self._bot_email = None
        self._webhook_server = None
        self._webhook_task = None
        
        # Credentials
        self._credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        self._project_id = (
            os.getenv("GOOGLE_CLOUD_PROJECT") or
            os.getenv("GCP_PROJECT") or
            os.getenv("GCLOUD_PROJECT")
        )
        self._webhook_secret = os.getenv("GOOGLE_CHAT_WEBHOOK_SECRET")
        
        # Cache for user info
        self._user_cache: Dict[str, Dict[str, Any]] = {}
        
    async def connect(self) -> bool:
        """Initialize Google Chat service and start webhook server."""
        if not HAS_GOOGLE_API:
            logger.error("[Google Chat] google-api-python-client not installed")
            return False
        
        if not self._project_id:
            logger.error("[Google Chat] No GCP project ID set")
            return False
        
        try:
            # Build Chat API client
            if self._credentials_path and os.path.exists(self._credentials_path):
                credentials = service_account.Credentials.from_service_account_file(
                    self._credentials_path,
                    scopes=["https://www.googleapis.com/auth/chat.bot"]
                )
            else:
                # Try ADC
                from google.auth import default
                credentials, _ = default(scopes=["https://www.googleapis.com/auth/chat.bot"])
            
            self._chat_service = build("chat", "v1", credentials=credentials)
            
            # Get bot info
            bot_info = await self._get_bot_info()
            if bot_info:
                self._bot_email = bot_info.get("email")
                logger.info("[Google Chat] Connected as %s", self._bot_email)
            
            # Start webhook server for incoming messages
            await self._start_webhook_server()
            
            return True
            
        except Exception as e:
            logger.exception("[Google Chat] Connection failed")
            return False
    
    async def _get_bot_info(self) -> Optional[Dict[str, Any]]:
        """Get bot's own user info."""
        try:
            # List spaces to verify connection and get bot info
            # Note: Chat API v1 doesn't have a direct "get bot" endpoint
            # We infer from the credentials
            return {"email": f"bot@{self._project_id}.iam.gserviceaccount.com"}
        except Exception as e:
            logger.warning("[Google Chat] Could not get bot info: %s", e)
            return None
    
    async def _start_webhook_server(self):
        """Start HTTP server to receive Google Chat webhooks."""
        from fastapi import FastAPI, Request, HTTPException
        from fastapi.responses import JSONResponse
        import uvicorn
        
        app = FastAPI()
        
        @app.post("/google-chat/webhook")
        async def webhook_handler(request: Request):
            """Handle incoming Google Chat webhook."""
            try:
                # Validate webhook signature if secret is configured
                if self._webhook_secret:
                    signature = request.headers.get("X-Goog-Signature")
                    if not signature:
                        logger.warning("[Google Chat] Missing webhook signature")
                        raise HTTPException(status_code=401, detail="Missing signature")
                    
                    body = await request.body()
                    expected_sig = hmac.new(
                        self._webhook_secret.encode(),
                        body,
                        hashlib.sha256
                    ).digest()
                    
                    try:
                        decoded_sig = base64.b64decode(signature)
                        if not hmac.compare_digest(decoded_sig, expected_sig):
                            logger.warning("[Google Chat] Invalid webhook signature")
                            raise HTTPException(status_code=401, detail="Invalid signature")
                    except Exception:
                        logger.warning("[Google Chat] Signature validation failed")
                        raise HTTPException(status_code=401, detail="Invalid signature")
                
                payload = await request.json()
                logger.debug("[Google Chat] Webhook payload: %s", json.dumps(payload)[:500])
                
                # Process the message
                await self._handle_webhook_message(payload)
                
                # Google Chat expects empty 200 response
                return JSONResponse(content={})
                
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("[Google Chat] Webhook error")
                raise HTTPException(status_code=500, detail=str(e))
        
        # Start server
        config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning")
        self._webhook_server = uvicorn.Server(config)
        self._webhook_task = asyncio.create_task(self._webhook_server.serve())
        logger.info("[Google Chat] Webhook server started on port 8080")
    
    async def _handle_webhook_message(self, payload: Dict[str, Any]):
        """Process incoming webhook message from Google Chat."""
        try:
            # Extract message data
            message_data = payload.get("message", {})
            sender = payload.get("user", {})
            space = payload.get("space", {})
            
            text = message_data.get("text", "")
            if not text:
                # Could be a card interaction
                logger.debug("[Google Chat] No text in message, skipping")
                return
            
            # Determine chat type
            space_type = space.get("type", "SPACE")  # DM, SPACE, or UNKNOWN
            is_dm = space_type == "DM"
            
            # Build chat IDs
            space_id = space.get("name", "")  # Format: "spaces/SPACE_ID"
            space_short_id = space_id.split("/")[-1] if "/" in space_id else space_id
            
            sender_email = sender.get("email", "")
            sender_name = sender.get("name", sender.get("displayName", "Unknown"))
            
            # Build source
            source = self.build_source(
                chat_id=space_short_id,
                user_id=sender_email,
                user_name=sender_name,
                platform_specific={
                    "space_id": space_id,
                    "space_type": space_type,
                    "message_id": message_data.get("name", ""),
                    "thread_id": message_data.get("thread", {}).get("name") if message_data.get("thread") else None,
                }
            )
            
            # Create message event
            event = MessageEvent(
                source=source,
                message_type=MessageType.TEXT,
                content=text,
                is_group=not is_dm,
                timestamp=datetime.utcnow().isoformat(),
            )
            
            # Dispatch to gateway
            await self.handle_message(event)
            
        except Exception as e:
            logger.exception("[Google Chat] Error handling webhook message")
    
    async def disconnect(self):
        """Disconnect from Google Chat."""
        if self._webhook_task:
            self._webhook_task.cancel()
            try:
                await self._webhook_task
            except asyncio.CancelledError:
                pass
        
        if self._webhook_server:
            await self._webhook_server.shutdown()
        
        self._chat_service = None
        logger.info("[Google Chat] Disconnected")
    
    async def send(self, chat_id: str, text: str, **kwargs) -> SendResult:
        """Send a text message to a Google Chat space or DM."""
        if not self._chat_service:
            return SendResult(success=False, error="Not connected")
        
        try:
            # Format: "spaces/SPACE_ID"
            space_name = f"spaces/{chat_id}"
            
            # Check if this is a thread reply
            thread_id = kwargs.get("thread_id")
            message_payload = {"text": text}
            
            if thread_id:
                message_payload["thread"] = {"name": thread_id}
            
            response = self._chat_service.spaces().messages().create(
                parent=space_name,
                body=message_payload
            ).execute()
            
            message_id = response.get("name", "")
            logger.debug("[Google Chat] Sent message %s to %s", message_id, chat_id)
            
            return SendResult(
                success=True,
                message_id=message_id,
                chat_id=chat_id,
            )
            
        except HttpError as e:
            logger.error("[Google Chat] Send failed: %s", e)
            return SendResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("[Google Chat] Unexpected send error")
            return SendResult(success=False, error=str(e))
    
    async def send_typing(self, chat_id: str):
        """Send typing indicator (not supported by Google Chat API)."""
        # Google Chat doesn't support typing indicators via API
        pass
    
    async def get_chat_info(self, chat_id: str) -> dict:
        """Get information about a space."""
        if not self._chat_service:
            return {"name": chat_id, "type": "unknown", "chat_id": chat_id}
        
        try:
            space_name = f"spaces/{chat_id}"
            response = self._chat_service.spaces().get(name=space_name).execute()
            
            return {
                "name": response.get("displayName", chat_id),
                "type": response.get("type", "SPACE").lower(),
                "chat_id": chat_id,
            }
        except Exception as e:
            logger.warning("[Google Chat] Could not get chat info for %s: %s", chat_id, e)
            return {"name": chat_id, "type": "unknown", "chat_id": chat_id}
    
    async def send_image(self, chat_id: str, image_url: str, caption: str = None) -> SendResult:
        """Send an image to Google Chat."""
        if not self._chat_service:
            return SendResult(success=False, error="Not connected")
        
        try:
            space_name = f"spaces/{chat_id}"
            
            # Google Chat requires images to be uploaded or provided as Drive links
            # For now, we'll send as a text message with the image URL
            # TODO: Implement proper image upload via Drive API
            
            text = caption or "Image"
            if image_url:
                text = f"{text}\n\n![Image]({image_url})" if text else f"![Image]({image_url})"
            
            response = self._chat_service.spaces().messages().create(
                parent=space_name,
                body={"text": text}
            ).execute()
            
            return SendResult(
                success=True,
                message_id=response.get("name", ""),
                chat_id=chat_id,
            )
            
        except HttpError as e:
            logger.error("[Google Chat] Image send failed: %s", e)
            return SendResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("[Google Chat] Unexpected image send error")
            return SendResult(success=False, error=str(e))


# Platform hint for agent
GOOGLE_CHAT_HINT = (
    "You are on Google Chat. Messages support basic Markdown formatting. "
    "Images can be shared via URLs. Thread replies are supported. "
    "Google Chat is used within Google Workspace environments."
)
