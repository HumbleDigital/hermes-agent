"""Tests for the Google Chat adapter and shared auth helpers."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageType
from gateway.platforms.google_chat import (
    HAS_GOOGLE_API,
    GoogleChatAdapter,
    check_google_chat_requirements,
    normalize_google_chat_webhook_payload,
)
from gateway.platforms.google_chat_auth import (
    GOOGLE_CHAT_BOT_SCOPE,
    GOOGLE_CHAT_SPACES_CREATE_SCOPE,
    build_google_chat_app_credentials,
    build_google_chat_dwd_user_credentials,
)


pytestmark = pytest.mark.skipif(not HAS_GOOGLE_API, reason="google-api-python-client not installed")


class TestGoogleChatRequirements:
    def test_false_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "gateway.platforms.google_chat_auth.Path.exists",
            return_value=False,
        ):
            assert check_google_chat_requirements() is False

    def test_true_with_credentials_and_project(self):
        env = {
            "GOOGLE_APPLICATION_CREDENTIALS": "/fake/service-account.json",
            "GOOGLE_CLOUD_PROJECT": "test-project",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "gateway.platforms.google_chat_auth.Path.exists",
            return_value=True,
        ):
            assert check_google_chat_requirements() is True


class TestGoogleChatAuthHelpers:
    def test_build_app_credentials_uses_bot_scope(self):
        mock_creds = MagicMock()
        with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "/fake/key.json"}, clear=True), patch(
            "gateway.platforms.google_chat_auth.Path.exists",
            return_value=True,
        ), patch(
            "gateway.platforms.google_chat_auth.service_account.Credentials.from_service_account_file",
            return_value=mock_creds,
        ) as mock_build:
            result = build_google_chat_app_credentials()

        assert result is mock_creds
        mock_build.assert_called_once_with("/fake/key.json", scopes=[GOOGLE_CHAT_BOT_SCOPE])

    def test_build_dwd_credentials_uses_subject_and_setup_scope(self):
        mock_creds = MagicMock()
        with patch.dict(
            "os.environ",
            {"GOOGLE_APPLICATION_CREDENTIALS": "/fake/key.json"},
            clear=True,
        ), patch(
            "gateway.platforms.google_chat_auth.Path.exists",
            return_value=True,
        ), patch(
            "gateway.platforms.google_chat_auth.service_account.Credentials.from_service_account_file",
            return_value=mock_creds,
        ) as mock_build:
            result = build_google_chat_dwd_user_credentials("human@example.com")

        assert result is mock_creds
        mock_build.assert_called_once_with(
            "/fake/key.json",
            scopes=[GOOGLE_CHAT_SPACES_CREATE_SCOPE],
            subject="human@example.com",
        )


class TestGoogleChatAdapterLifecycle:
    @pytest.mark.asyncio
    async def test_connect_send_info_and_disconnect(self):
        mock_credentials = MagicMock()
        mock_credentials.service_account_email = "bot@example.com"
        mock_service = MagicMock()
        mock_service.spaces.return_value.messages.return_value.create.return_value.execute.return_value = {
            "name": "spaces/test-space/messages/123",
        }
        mock_service.spaces.return_value.get.return_value.execute.return_value = {
            "displayName": "Test Space",
            "type": "SPACE",
        }

        with patch.dict(
            os.environ,
            {
                "GOOGLE_APPLICATION_CREDENTIALS": "/fake/key.json",
                "GOOGLE_CLOUD_PROJECT": "test-project",
            },
            clear=True,
        ), patch(
            "gateway.platforms.google_chat_auth.Path.exists",
            return_value=True,
        ), patch(
            "gateway.platforms.google_chat.build_google_chat_app_credentials",
            return_value=mock_credentials,
        ), patch(
            "gateway.platforms.google_chat.build",
            return_value=mock_service,
        ):
            adapter = GoogleChatAdapter(PlatformConfig())
            adapter._start_webhook_server = AsyncMock()
            assert await adapter.connect() is True
            assert adapter._bot_email == "bot@example.com"

            send_result = await adapter.send("test-space", "hello there")
            assert send_result.success is True
            assert send_result.message_id == "spaces/test-space/messages/123"

            info = await adapter.get_chat_info("test-space")
            assert info["name"] == "Test Space"
            assert info["type"] == "space"

            await adapter.disconnect()

        assert adapter._chat_service is None
        assert adapter._connected is False
        adapter._start_webhook_server.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_result_regression_no_unsupported_kwargs(self):
        mock_credentials = MagicMock()
        mock_service = MagicMock()
        mock_service.spaces.return_value.messages.return_value.create.return_value.execute.return_value = {
            "name": "spaces/test-space/messages/999",
        }

        with patch.dict(
            os.environ,
            {
                "GOOGLE_APPLICATION_CREDENTIALS": "/fake/key.json",
                "GOOGLE_CLOUD_PROJECT": "test-project",
            },
            clear=True,
        ), patch(
            "gateway.platforms.google_chat_auth.Path.exists",
            return_value=True,
        ), patch(
            "gateway.platforms.google_chat.build_google_chat_app_credentials",
            return_value=mock_credentials,
        ), patch(
            "gateway.platforms.google_chat.build",
            return_value=mock_service,
        ):
            adapter = GoogleChatAdapter(PlatformConfig())
            adapter._start_webhook_server = AsyncMock()
            assert await adapter.connect() is True
            result = await adapter.send("test-space", "regression check")

        assert result.success is True
        assert result.message_id == "spaces/test-space/messages/999"


class TestGoogleChatWebhookNormalization:
    def test_normalize_webhook_payload(self):
        payload = {
            "message": {
                "text": "hello",
                "name": "spaces/abc/messages/123",
                "thread": {"name": "spaces/abc/threads/t-1"},
            },
            "user": {"email": "person@example.com", "displayName": "Person"},
            "space": {"name": "spaces/abc", "type": "SPACE"},
        }

        normalized = normalize_google_chat_webhook_payload(payload)
        assert normalized["text"] == "hello"
        assert normalized["space_short_id"] == "abc"
        assert normalized["sender_email"] == "person@example.com"
        assert normalized["is_dm"] is False

    @pytest.mark.asyncio
    async def test_handle_webhook_dispatches_message_event(self):
        adapter = GoogleChatAdapter(PlatformConfig())
        adapter.handle_message = AsyncMock()

        payload = {
            "message": {
                "text": "ping",
                "name": "spaces/abc/messages/123",
            },
            "user": {"email": "person@example.com", "displayName": "Person"},
            "space": {"name": "spaces/abc", "type": "SPACE"},
        }

        await adapter._handle_webhook_message(payload)

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.await_args.args[0]
        assert event.message_type is MessageType.TEXT
        assert event.text == "ping"
        assert event.source.chat_type == "group"
        assert event.source.chat_id == "abc"
        assert event.source.user_id == "person@example.com"
