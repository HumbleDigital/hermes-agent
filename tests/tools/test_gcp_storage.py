"""Tests for tools/gcp_storage.py."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools.gcp_storage import (
    HAS_GCP,
    check_gcp_storage_requirements,
    create_bucket,
    delete_blob,
    delete_bucket,
    download_file,
    list_blobs,
    list_buckets,
    upload_file,
)


pytestmark = pytest.mark.skipif(not HAS_GCP, reason="google-cloud-storage not installed")


def _bucket_obj(name: str, location="us-east1", storage_class="STANDARD"):
    bucket = SimpleNamespace(
        name=name,
        location=location,
        storage_class=storage_class,
        time_created=datetime(2026, 1, 1),
    )
    bucket.get_iam_policy = MagicMock(return_value={"bindings": []})
    bucket.list_blobs = MagicMock(return_value=[])
    bucket.blob = MagicMock(side_effect=lambda blob_name: SimpleNamespace(name=blob_name))
    bucket.delete = MagicMock()
    return bucket


class TestGcpStorageRequirements:
    def test_false_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True), patch("os.path.exists", return_value=False):
            assert check_gcp_storage_requirements() is False

    def test_true_with_adc_and_project(self):
        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
        with patch.dict(os.environ, env, clear=True), patch("os.path.exists") as mock_exists:
            mock_exists.side_effect = lambda path: path == adc_path
            assert check_gcp_storage_requirements() is True


class TestBuckets:
    def test_create_bucket_envelope(self):
        mock_client = MagicMock()
        mock_bucket = _bucket_obj("phase1-bucket")
        mock_client.bucket.return_value = mock_bucket
        mock_client.create_bucket.return_value = mock_bucket

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_storage._get_client",
            return_value=mock_client,
        ):
            result = json.loads(create_bucket("phase1-bucket", project_id="test-project"))

        assert result["success"] is True
        assert result["bucket_name"] == "phase1-bucket"
        mock_client.create_bucket.assert_called_once()

    def test_list_buckets_envelope(self):
        mock_client = MagicMock()
        mock_client.list_buckets.return_value = [
            _bucket_obj("phase1-a"),
            _bucket_obj("phase1-b", storage_class="NEARLINE"),
        ]

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_storage._get_client",
            return_value=mock_client,
        ):
            result = json.loads(list_buckets(project_id="test-project", limit=10))

        assert result["success"] is True
        assert result["count"] == 2
        assert result["buckets"][0]["name"] == "phase1-a"

    def test_delete_bucket_not_found(self):
        from google.api_core.exceptions import NotFound

        mock_client = MagicMock()
        mock_bucket = _bucket_obj("phase1-bucket")
        mock_bucket.delete.side_effect = NotFound("missing")
        mock_client.bucket.return_value = mock_bucket

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_storage._get_client",
            return_value=mock_client,
        ):
            result = json.loads(delete_bucket("phase1-bucket", project_id="test-project"))

        assert "not found" in result["error"].lower()


class TestObjects:
    def test_upload_download_list_delete_round_trip(self, tmp_path):
        mock_client = MagicMock()
        blob = SimpleNamespace(
            name="docs/example.txt",
            size=12,
            updated=datetime(2026, 1, 1),
            media_link="https://example.invalid",
            content_type="text/plain",
            md5_hash="abc123",
        )
        blob.upload_from_filename = MagicMock()
        blob.download_to_filename = MagicMock(side_effect=lambda path: Path(path).write_text("hello world", encoding="utf-8"))
        blob.delete = MagicMock()

        bucket = _bucket_obj("phase1-bucket")
        bucket.blob = MagicMock(return_value=blob)
        bucket.list_blobs = MagicMock(return_value=[blob])
        mock_client.bucket.return_value = bucket

        source = tmp_path / "source.txt"
        source.write_text("hello world", encoding="utf-8")
        destination = tmp_path / "nested" / "downloaded.txt"

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_storage._get_client",
            return_value=mock_client,
        ):
            upload_result = json.loads(
                upload_file("phase1-bucket", str(source), destination_blob="docs/example.txt", project_id="test-project")
            )
            list_result = json.loads(list_blobs("phase1-bucket", project_id="test-project", limit=10))
            download_result = json.loads(
                download_file("phase1-bucket", "docs/example.txt", str(destination), project_id="test-project")
            )
            delete_result = json.loads(delete_blob("phase1-bucket", "docs/example.txt", project_id="test-project"))

        assert upload_result["success"] is True
        assert list_result["success"] is True
        assert download_result["success"] is True
        assert destination.read_text(encoding="utf-8") == "hello world"
        assert delete_result["success"] is True

    def test_upload_missing_file_returns_error(self, tmp_path):
        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True):
            result = json.loads(upload_file("phase1-bucket", str(tmp_path / "missing.txt"), project_id="test-project"))

        assert "File not found" in result["error"]

    def test_download_creates_destination_dir(self, tmp_path):
        mock_client = MagicMock()
        blob = SimpleNamespace(name="docs/example.txt", size=12, updated=None)
        blob.download_to_filename = MagicMock(side_effect=lambda path: Path(path).write_text("payload", encoding="utf-8"))
        bucket = _bucket_obj("phase1-bucket")
        bucket.blob = MagicMock(return_value=blob)
        mock_client.bucket.return_value = bucket

        destination = tmp_path / "nested" / "dir" / "downloaded.txt"

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_storage._get_client",
            return_value=mock_client,
        ):
            result = json.loads(
                download_file("phase1-bucket", "docs/example.txt", str(destination), project_id="test-project")
            )

        assert result["success"] is True
        assert destination.read_text(encoding="utf-8") == "payload"


class TestErrorMapping:
    def test_permission_denied_on_create_bucket(self):
        from google.api_core.exceptions import PermissionDenied

        mock_client = MagicMock()
        mock_client.bucket.side_effect = PermissionDenied("denied")

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_storage._get_client",
            return_value=mock_client,
        ):
            result = json.loads(create_bucket("phase1-bucket", project_id="test-project"))

        assert "Permission denied" in result["error"]

    def test_generic_api_error_on_list_blobs(self):
        from google.api_core.exceptions import GoogleAPIError

        mock_client = MagicMock()
        bucket = _bucket_obj("phase1-bucket")
        bucket.list_blobs.side_effect = GoogleAPIError("boom")
        mock_client.bucket.return_value = bucket

        with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}, clear=True), patch(
            "tools.gcp_storage._get_client",
            return_value=mock_client,
        ):
            result = json.loads(list_blobs("phase1-bucket", project_id="test-project"))

        assert "Google Cloud API error" in result["error"]
