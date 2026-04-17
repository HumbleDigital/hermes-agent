"""Tests for tools/gcp_secret_manager.py - Google Cloud Secret Manager integration."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from tools.gcp_secret_manager import (
    HAS_GCP,
    check_gcp_secret_manager_requirements,
    list_secrets,
    read_secret,
    write_secret,
)


# Skip all tests if the GCP library isn't installed (shouldn't happen in test env)
pytestmark = pytest.mark.skipif(not HAS_GCP, reason="google-cloud-secret-manager not installed")


class TestCheckGcpSecretManagerRequirements:
    """Tests for the requirements check function."""

    def test_returns_false_without_credentials(self):
        """Should return False when no credentials exist."""
        with patch.dict(os.environ, {}, clear=True):
            # Ensure no credential files exist
            with patch("os.path.exists", return_value=False):
                assert check_gcp_secret_manager_requirements() is False

    def test_returns_false_without_project_id(self):
        """Should return False when credentials exist but no project ID."""
        with patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "/fake/path.json"}):
            with patch("os.path.exists", return_value=True):
                assert check_gcp_secret_manager_requirements() is False

    def test_returns_true_with_adc_and_project(self):
        """Should return True when ADC exists with project ID."""
        env = {
            "GOOGLE_CLOUD_PROJECT": "test-project",
        }
        with patch.dict(os.environ, env, clear=True):
            adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
            with patch("os.path.exists") as mock_exists:
                # Mock ADC path to exist, others to not
                def exists_side_effect(path):
                    if path == adc_path:
                        return True
                    return False

                mock_exists.side_effect = exists_side_effect
                assert check_gcp_secret_manager_requirements() is True

    def test_returns_true_with_explicit_credentials_and_project(self):
        """Should return True when explicit credentials file exists with project ID."""
        env = {
            "GOOGLE_APPLICATION_CREDENTIALS": "/fake/creds.json",
            "GOOGLE_CLOUD_PROJECT": "test-project",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("os.path.exists", return_value=True):
                assert check_gcp_secret_manager_requirements() is True

    def test_accepts_alternative_project_env_vars(self):
        """Should accept GCP_PROJECT or GCLOUD_PROJECT as alternatives."""
        for project_var in ["GCP_PROJECT", "GCLOUD_PROJECT"]:
            env = {
                project_var: "test-project",
            }
            with patch.dict(os.environ, env, clear=True):
                adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
                with patch("os.path.exists") as mock_exists:
                    mock_exists.side_effect = lambda p: p == adc_path
                    assert check_gcp_secret_manager_requirements() is True, f"Failed for {project_var}"


class TestReadSecret:
    """Tests for the read_secret function."""

    def test_returns_error_without_gcp_library(self):
        """Should return error if GCP library not installed."""
        with patch("tools.gcp_secret_manager.HAS_GCP", False):
            result = json.loads(read_secret("test-secret"))
            assert "error" in result
            assert "not installed" in result["error"]

    def test_returns_error_without_project_id(self):
        """Should return error if no project ID provided."""
        with patch.dict(os.environ, {}, clear=True):
            result = json.loads(read_secret("test-secret"))
            assert "error" in result
            assert "project ID" in result["error"]

    def test_reads_secret_successfully(self):
        """Should return secret value on successful read."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.payload.data = b"secret-value-123"
        mock_client.access_secret_version.return_value = mock_response

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(read_secret("my-secret", version_id="latest"))

                assert result["success"] is True
                assert result["secret_id"] == "my-secret"
                assert result["value"] == "secret-value-123"
                assert result["version"] == "latest"

    def test_reads_specific_version(self):
        """Should use provided version ID."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.payload.data = b"v2-value"
        mock_client.access_secret_version.return_value = mock_response

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(read_secret("my-secret", version_id="42"))

                mock_client.access_secret_version.assert_called_once_with(
                    request={"name": "projects/test-project/secrets/my-secret/versions/42"}
                )
                assert result["value"] == "v2-value"

    def test_uses_explicit_project_id(self):
        """Should use explicit project_id parameter over env var."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.payload.data = b"value"
        mock_client.access_secret_version.return_value = mock_response

        with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
            mock_client_class.return_value = mock_client

            result = json.loads(read_secret("my-secret", project_id="explicit-project"))

            mock_client.access_secret_version.assert_called_once_with(
                request={"name": "projects/explicit-project/secrets/my-secret/versions/latest"}
            )
            assert result["success"] is True

    def test_returns_error_on_not_found(self):
        """Should return error when secret doesn't exist."""
        from google.api_core.exceptions import NotFound

        mock_client = MagicMock()
        mock_client.access_secret_version.side_effect = NotFound("Secret not found")

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(read_secret("nonexistent-secret"))

                assert "error" in result
                assert "not found" in result["error"]
                assert "nonexistent-secret" in result["error"]

    def test_returns_error_on_permission_denied(self):
        """Should return error on permission denied."""
        from google.api_core.exceptions import PermissionDenied

        mock_client = MagicMock()
        mock_client.access_secret_version.side_effect = PermissionDenied("Access denied")

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(read_secret("restricted-secret"))

                assert "error" in result
                assert "Permission denied" in result["error"]
                assert "secretAccessor" in result["error"]

    def test_returns_error_on_generic_api_error(self):
        """Should return error on generic GoogleAPIError."""
        from google.api_core.exceptions import GoogleAPIError

        mock_client = MagicMock()
        mock_client.access_secret_version.side_effect = GoogleAPIError("Network error")

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(read_secret("my-secret"))

                assert "error" in result
                assert "Google Cloud API error" in result["error"]

    def test_returns_error_on_unexpected_exception(self):
        """Should return error on unexpected exceptions."""
        mock_client = MagicMock()
        mock_client.access_secret_version.side_effect = RuntimeError("Something went wrong")

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(read_secret("my-secret"))

                assert "error" in result
                assert "Unexpected error" in result["error"]


class TestWriteSecret:
    """Tests for the write_secret function."""

    def test_returns_error_without_gcp_library(self):
        """Should return error if GCP library not installed."""
        with patch("tools.gcp_secret_manager.HAS_GCP", False):
            result = json.loads(write_secret("test-secret", "value"))
            assert "error" in result
            assert "not installed" in result["error"]

    def test_returns_error_without_project_id(self):
        """Should return error if no project ID provided."""
        with patch.dict(os.environ, {}, clear=True):
            result = json.loads(write_secret("test-secret", "value"))
            assert "error" in result
            assert "project ID" in result["error"]

    def test_creates_new_secret_successfully(self):
        """Should create new secret and add version."""
        mock_client = MagicMock()
        mock_create_response = MagicMock()
        mock_add_response = MagicMock()
        mock_add_response.name = "projects/test-project/secrets/my-secret/versions/1"

        mock_client.create_secret.return_value = mock_create_response
        mock_client.add_secret_version.return_value = mock_add_response

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(write_secret("my-secret", "secret-value-123"))

                assert result["success"] is True
                assert result["secret_id"] == "my-secret"
                assert "updated successfully" in result["message"]
                assert mock_client.create_secret.called
                assert mock_client.add_secret_version.called

    def test_adds_version_to_existing_secret(self):
        """Should add version when secret already exists."""
        from google.api_core.exceptions import GoogleAPIError

        mock_client = MagicMock()
        mock_add_response = MagicMock()
        mock_add_response.name = "projects/test-project/secrets/existing-secret/versions/2"

        # First call (create_secret) raises Already exists
        mock_client.create_secret.side_effect = GoogleAPIError("Already exists")
        mock_client.add_secret_version.return_value = mock_add_response

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(write_secret("existing-secret", "new-value"))

                assert result["success"] is True
                assert mock_client.create_secret.called
                assert mock_client.add_secret_version.called

    def test_uses_explicit_project_id(self):
        """Should use explicit project_id parameter over env var."""
        mock_client = MagicMock()
        mock_add_response = MagicMock()
        mock_add_response.name = "projects/explicit-project/secrets/my-secret/versions/1"

        mock_client.add_secret_version.return_value = mock_add_response

        with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
            mock_client_class.return_value = mock_client

            result = json.loads(write_secret("my-secret", "value", project_id="explicit-project"))

            # Check that parent path uses explicit project
            call_args = mock_client.add_secret_version.call_args
            assert "explicit-project" in call_args[1]["request"]["parent"]
            assert result["success"] is True

    def test_returns_error_on_permission_denied(self):
        """Should return error on permission denied."""
        from google.api_core.exceptions import PermissionDenied

        mock_client = MagicMock()
        mock_client.create_secret.side_effect = PermissionDenied("Access denied")

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(write_secret("my-secret", "value"))

                assert "error" in result
                assert "Permission denied" in result["error"]
                assert "secretmanager.admin" in result["error"]

    def test_returns_error_on_generic_api_error(self):
        """Should return error on generic GoogleAPIError (not Already exists)."""
        from google.api_core.exceptions import GoogleAPIError

        mock_client = MagicMock()
        mock_client.create_secret.side_effect = GoogleAPIError("Quota exceeded")

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(write_secret("my-secret", "value"))

                assert "error" in result
                assert "Google Cloud API error" in result["error"]

    def test_returns_error_on_unexpected_exception(self):
        """Should return error on unexpected exceptions."""
        mock_client = MagicMock()
        mock_client.create_secret.side_effect = RuntimeError("Something went wrong")

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(write_secret("my-secret", "value"))

                assert "error" in result
                assert "Unexpected error" in result["error"]


class TestListSecrets:
    """Tests for the list_secrets function."""

    def test_returns_error_without_gcp_library(self):
        """Should return error if GCP library not installed."""
        with patch("tools.gcp_secret_manager.HAS_GCP", False):
            result = json.loads(list_secrets())
            assert "error" in result
            assert "not installed" in result["error"]

    def test_returns_error_without_project_id(self):
        """Should return error if no project ID provided."""
        with patch.dict(os.environ, {}, clear=True):
            result = json.loads(list_secrets())
            assert "error" in result
            assert "project ID" in result["error"]

    def test_lists_secrets_successfully(self):
        """Should return list of secrets."""
        mock_client = MagicMock()
        mock_secret1 = MagicMock()
        mock_secret1.name = "projects/test-project/secrets/secret-one"
        mock_secret1.create_time = "2024-01-01T00:00:00Z"
        mock_secret1.replication.automatic = True

        mock_secret2 = MagicMock()
        mock_secret2.name = "projects/test-project/secrets/secret-two"
        mock_secret2.create_time = None
        mock_secret2.replication.automatic = False

        mock_client.list_secrets.return_value = [mock_secret1, mock_secret2]

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(list_secrets())

                assert result["success"] is True
                assert result["project_id"] == "test-project"
                assert result["count"] == 2
                assert len(result["secrets"]) == 2
                assert result["secrets"][0]["name"] == "secret-one"
                assert result["secrets"][0]["replication"] == "automatic"
                assert result["secrets"][1]["name"] == "secret-two"
                assert result["secrets"][1]["replication"] == "custom"

    def test_respects_limit_parameter(self):
        """Should respect the limit parameter."""
        mock_client = MagicMock()
        mock_secrets = []
        for i in range(10):
            mock_secret = MagicMock()
            mock_secret.name = f"projects/test-project/secrets/secret-{i}"
            mock_secret.create_time = None
            mock_secret.replication.automatic = True
            mock_secrets.append(mock_secret)

        mock_client.list_secrets.return_value = mock_secrets

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(list_secrets(limit=3))

                assert result["count"] == 3
                assert len(result["secrets"]) == 3

    def test_uses_explicit_project_id(self):
        """Should use explicit project_id parameter over env var."""
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = []

        with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
            mock_client_class.return_value = mock_client

            result = json.loads(list_secrets(project_id="explicit-project"))

            call_args = mock_client.list_secrets.call_args
            assert "explicit-project" in call_args[1]["request"]["parent"]
            assert result["success"] is True

    def test_returns_error_on_permission_denied(self):
        """Should return error on permission denied."""
        from google.api_core.exceptions import PermissionDenied

        mock_client = MagicMock()
        mock_client.list_secrets.side_effect = PermissionDenied("Access denied")

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(list_secrets())

                assert "error" in result
                assert "Permission denied" in result["error"]
                assert "secretmanager.viewer" in result["error"]

    def test_returns_error_on_generic_api_error(self):
        """Should return error on generic GoogleAPIError."""
        from google.api_core.exceptions import GoogleAPIError

        mock_client = MagicMock()
        mock_client.list_secrets.side_effect = GoogleAPIError("Network error")

        env = {"GOOGLE_CLOUD_PROJECT": "test-project"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tools.gcp_secret_manager.secretmanager.SecretManagerServiceClient") as mock_client_class:
                mock_client_class.return_value = mock_client

                result = json.loads(list_secrets())

                assert "error" in result
                assert "Google Cloud API error" in result["error"]


class TestSchemas:
    """Tests for the OpenAI function-calling schemas."""

    def test_read_schema_name(self):
        """Read schema should have correct name."""
        from tools.gcp_secret_manager import READ_SECRET_SCHEMA
        assert READ_SECRET_SCHEMA["name"] == "gcp_secret_read"

    def test_read_schema_required_params(self):
        """Read schema should have secret_id as required."""
        from tools.gcp_secret_manager import READ_SECRET_SCHEMA
        assert "secret_id" in READ_SECRET_SCHEMA["parameters"]["required"]
        assert "version_id" not in READ_SECRET_SCHEMA["parameters"]["required"]
        assert "project_id" not in READ_SECRET_SCHEMA["parameters"]["required"]

    def test_write_schema_name(self):
        """Write schema should have correct name."""
        from tools.gcp_secret_manager import WRITE_SECRET_SCHEMA
        assert WRITE_SECRET_SCHEMA["name"] == "gcp_secret_write"

    def test_write_schema_required_params(self):
        """Write schema should have secret_id and secret_value as required."""
        from tools.gcp_secret_manager import WRITE_SECRET_SCHEMA
        assert "secret_id" in WRITE_SECRET_SCHEMA["parameters"]["required"]
        assert "secret_value" in WRITE_SECRET_SCHEMA["parameters"]["required"]
        assert "project_id" not in WRITE_SECRET_SCHEMA["parameters"]["required"]

    def test_list_schema_name(self):
        """List schema should have correct name."""
        from tools.gcp_secret_manager import LIST_SECRETS_SCHEMA
        assert LIST_SECRETS_SCHEMA["name"] == "gcp_secret_list"

    def test_list_schema_no_required_params(self):
        """List schema should have no required parameters."""
        from tools.gcp_secret_manager import LIST_SECRETS_SCHEMA
        assert LIST_SECRETS_SCHEMA["parameters"]["required"] == []
