"""Tests for tools/gcp_tasks.py."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools.gcp_tasks import (
    HAS_GCP,
    check_gcp_tasks_requirements,
    create_queue,
    create_task,
    delete_task,
    list_queues,
    list_tasks,
)


pytestmark = pytest.mark.skipif(not HAS_GCP, reason="google-cloud-tasks not installed")


def _queue_obj(name: str, rate: float | None = None, concurrent: int | None = None):
    rate_limits = None
    if rate is not None or concurrent is not None:
        rate_limits = SimpleNamespace(
            max_dispatches_per_second=rate,
            max_concurrent_dispatches=concurrent,
        )
    return SimpleNamespace(name=name, rate_limits=rate_limits)


def _task_obj(name: str, body: bytes | None = None, schedule_time=None):
    http_request = SimpleNamespace(body=body)
    return SimpleNamespace(name=name, http_request=http_request, schedule_time=schedule_time)


class TestGcpTasksRequirements:
    def test_false_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True), patch("os.path.exists", return_value=False):
            assert check_gcp_tasks_requirements() is False

    def test_true_with_adc_and_project(self):
        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
        with patch.dict(os.environ, env, clear=True), patch("os.path.exists") as mock_exists:
            mock_exists.side_effect = lambda path: path == adc_path
            assert check_gcp_tasks_requirements() is True


class TestCreateQueue:
    def test_creates_queue_with_short_name(self):
        mock_client = MagicMock()
        mock_response = SimpleNamespace(
            name="projects/test-project/locations/us-east1/queues/phase1-queue",
        )
        mock_client.create_queue.return_value = mock_response

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_tasks._get_client",
            return_value=mock_client,
        ):
            result = json.loads(create_queue("phase1-queue", project_id="test-project"))

        assert result["success"] is True
        assert result["queue_name"] == "phase1-queue"
        assert result["queue_path"].endswith("/phase1-queue")
        mock_client.create_queue.assert_called_once()

    def test_updates_existing_queue_when_create_reports_already_exists(self):
        from google.api_core.exceptions import GoogleAPIError

        mock_client = MagicMock()
        mock_client.create_queue.side_effect = GoogleAPIError("Already exists")
        mock_client.update_queue.return_value = SimpleNamespace(
            name="projects/test-project/locations/us-east1/queues/phase1-queue",
        )

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_tasks._get_client",
            return_value=mock_client,
        ):
            result = json.loads(create_queue("phase1-queue", project_id="test-project"))

        assert result["success"] is True
        mock_client.update_queue.assert_called_once()

    def test_permission_denied_maps_to_error(self):
        from google.api_core.exceptions import PermissionDenied

        mock_client = MagicMock()
        mock_client.create_queue.side_effect = PermissionDenied("denied")

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_tasks._get_client",
            return_value=mock_client,
        ):
            result = json.loads(create_queue("phase1-queue", project_id="test-project"))

        assert "Permission denied" in result["error"]


class TestListQueues:
    def test_lists_queue_envelopes(self):
        mock_client = MagicMock()
        mock_client.list_queues.return_value = [
            _queue_obj("projects/test-project/locations/us-east1/queues/a", 1.5, 2),
            _queue_obj("projects/test-project/locations/us-east1/queues/b", None, None),
        ]

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_tasks._get_client",
            return_value=mock_client,
        ):
            result = json.loads(list_queues(project_id="test-project", limit=10))

        assert result["success"] is True
        assert result["count"] == 2
        assert result["queues"][0]["name"] == "a"
        assert result["queues"][0]["rate_limit"] == 1.5


class TestCreateTask:
    def test_uses_short_queue_name_and_custom_task_name(self):
        mock_client = MagicMock()
        mock_client.create_task.return_value = SimpleNamespace(
            name="projects/test-project/locations/us-east1/queues/phase1/tasks/custom-name",
        )

        mock_timestamp = MagicMock()
        mock_timestamp.FromDatetime = MagicMock()

        schedule_time = datetime.utcnow() + timedelta(minutes=5)
        with patch.dict(
            os.environ,
            {
                "GOOGLE_CLOUD_PROJECT": "test-project",
                "GCP_TASKS_HANDLER_URL": "https://handler.example.com/tasks",
            },
            clear=True,
        ), patch("tools.gcp_tasks._get_client", return_value=mock_client), patch(
            "tools.gcp_tasks.Timestamp",
            return_value=mock_timestamp,
        ):
            result = json.loads(
                create_task(
                    "phase1",
                    {"payload": "value"},
                    task_name="custom-name",
                    schedule_time=schedule_time,
                    project_id="test-project",
                )
            )

        assert result["success"] is True
        assert result["queue"].endswith("/queues/phase1")
        create_call = mock_client.create_task.call_args.kwargs["request"]
        assert create_call["parent"].endswith("/queues/phase1")
        assert create_call["task"]["name"].endswith("/tasks/custom-name")
        mock_timestamp.FromDatetime.assert_called_once_with(schedule_time)

    def test_full_queue_path_is_preserved(self):
        mock_client = MagicMock()
        mock_client.create_task.return_value = SimpleNamespace(
            name="projects/test-project/locations/us-east1/queues/existing/tasks/t-1",
        )

        with patch.dict(
            os.environ,
            {
                "GOOGLE_CLOUD_PROJECT": "test-project",
                "GCP_TASKS_HANDLER_URL": "https://handler.example.com/tasks",
            },
            clear=True,
        ), patch("tools.gcp_tasks._get_client", return_value=mock_client):
            result = json.loads(
                create_task(
                    "projects/test-project/locations/us-east1/queues/existing",
                    {"payload": "value"},
                    project_id="test-project",
                )
            )

        assert result["success"] is True
        assert result["queue"] == "projects/test-project/locations/us-east1/queues/existing"

    def test_permission_denied_maps_to_error(self):
        from google.api_core.exceptions import PermissionDenied

        mock_client = MagicMock()
        mock_client.create_task.side_effect = PermissionDenied("denied")

        with patch.dict(
            os.environ,
            {
                "GOOGLE_CLOUD_PROJECT": "test-project",
                "GCP_TASKS_HANDLER_URL": "https://handler.example.com/tasks",
            },
            clear=True,
        ), patch("tools.gcp_tasks._get_client", return_value=mock_client):
            result = json.loads(create_task("phase1", {"payload": "value"}, project_id="test-project"))

        assert "Permission denied" in result["error"]


class TestListTasks:
    def test_lists_task_envelopes(self):
        mock_client = MagicMock()
        mock_client.list_tasks.return_value = [
            _task_obj(
                "projects/test-project/locations/us-east1/queues/phase1/tasks/a",
                body=json.dumps({"priority": "P1", "task_name": "a", "created_at": "now"}).encode(),
            )
        ]

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_tasks._get_client",
            return_value=mock_client,
        ):
            result = json.loads(list_tasks("phase1", project_id="test-project", limit=10))

        assert result["success"] is True
        assert result["count"] == 1
        assert result["tasks"][0]["task_name"] == "a"

    def test_permission_denied_maps_to_error(self):
        from google.api_core.exceptions import PermissionDenied

        mock_client = MagicMock()
        mock_client.list_tasks.side_effect = PermissionDenied("denied")

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_tasks._get_client",
            return_value=mock_client,
        ):
            result = json.loads(list_tasks("phase1", project_id="test-project"))

        assert "Permission denied" in result["error"]


class TestDeleteTask:
    def test_not_found_maps_to_error(self):
        from google.api_core.exceptions import NotFound

        mock_client = MagicMock()
        mock_client.delete_task.side_effect = NotFound("missing")

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_tasks._get_client",
            return_value=mock_client,
        ):
            result = json.loads(delete_task("phase1", "task-1", project_id="test-project"))

        assert "not found" in result["error"].lower()

    def test_generic_api_error_maps_to_error(self):
        from google.api_core.exceptions import GoogleAPIError

        mock_client = MagicMock()
        mock_client.delete_task.side_effect = GoogleAPIError("boom")

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_tasks._get_client",
            return_value=mock_client,
        ):
            result = json.loads(delete_task("phase1", "task-1", project_id="test-project"))

        assert "Google Cloud API error" in result["error"]
