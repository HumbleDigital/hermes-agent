# tools/gcp_secret_manager.py
"""Google Cloud Secret Manager integration for Hermes Agent.

Allows reading and writing secrets from GCP Secret Manager instead of environment variables.
Useful for managing API keys, credentials, and sensitive configuration securely.

Requires:
- Google Cloud project with Secret Manager API enabled
- Service account with roles/secretmanager.secretAccessor (read) or roles/secretmanager.admin (write)
- Application Default Credentials (ADC) or GOOGLE_APPLICATION_CREDENTIALS env var
"""
import json
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from google.cloud import secretmanager
    from google.api_core.exceptions import NotFound, PermissionDenied, GoogleAPIError
    HAS_GCP = True
except ImportError:
    HAS_GCP = False
    logger.warning("Google Cloud Secret Manager client not installed. Tool will be unavailable.")


def check_gcp_secret_manager_requirements() -> bool:
    """Return True if GCP Secret Manager dependencies and credentials are available."""
    if not HAS_GCP:
        return False
    
    # Check for ADC or explicit credentials
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    
    # Either explicit credentials file or ADC must exist
    has_credentials = (
        (credentials_path and os.path.exists(credentials_path)) or
        os.path.exists(adc_path)
    )
    
    # Also check for project ID (required for secret operations)
    has_project = bool(
        os.getenv("GOOGLE_CLOUD_PROJECT") or
        os.getenv("GCP_PROJECT") or
        os.getenv("GCLOUD_PROJECT")
    )
    
    return has_credentials and has_project


def read_secret(secret_id: str, version_id: str = "latest", project_id: Optional[str] = None) -> str:
    """Read a secret from Google Cloud Secret Manager.
    
    Args:
        secret_id: The ID of the secret to read (e.g., "ollama-api-key")
        version_id: The version ID or "latest" (default: "latest")
        project_id: GCP project ID (optional, uses env var if not provided)
    
    Returns:
        JSON string with secret value or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Secret Manager client not installed. Run: pip install google-cloud-secret-manager"})
    
    # Determine project ID
    if not project_id:
        project_id = (
            os.getenv("GOOGLE_CLOUD_PROJECT") or
            os.getenv("GCP_PROJECT") or
            os.getenv("GCLOUD_PROJECT")
        )
    
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    try:
        client = secretmanager.SecretManagerServiceClient()
        secret_name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
        
        logger.info(f"Reading secret: {secret_name}")
        response = client.access_secret_version(request={"name": secret_name})
        
        secret_value = response.payload.data.decode("UTF-8")
        return json.dumps({
            "success": True,
            "secret_id": secret_id,
            "version": version_id,
            "value": secret_value
        })
    
    except NotFound:
        return json.dumps({"error": f"Secret '{secret_id}' not found in project '{project_id}'"})
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied accessing secret '{secret_id}'. Ensure service account has roles/secretmanager.secretAccessor"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error reading secret {secret_id}")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


def write_secret(secret_id: str, secret_value: str, project_id: Optional[str] = None) -> str:
    """Create or update a secret in Google Cloud Secret Manager.
    
    Args:
        secret_id: The ID of the secret to create/update (e.g., "ollama-api-key")
        secret_value: The secret value to store
        project_id: GCP project ID (optional, uses env var if not provided)
    
    Returns:
        JSON string with success status or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Secret Manager client not installed. Run: pip install google-cloud-secret-manager"})
    
    # Determine project ID
    if not project_id:
        project_id = (
            os.getenv("GOOGLE_CLOUD_PROJECT") or
            os.getenv("GCP_PROJECT") or
            os.getenv("GCLOUD_PROJECT")
        )
    
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    try:
        client = secretmanager.SecretManagerServiceClient()
        parent = f"projects/{project_id}"
        secret_path = f"projects/{project_id}/secrets/{secret_id}"
        
        # Try to create the secret first (if it doesn't exist)
        try:
            secret = {"replication": {"automatic": {}}}
            response = client.create_secret(
                request={"parent": parent, "secret_id": secret_id, "secret": secret}
            )
            logger.info(f"Created new secret: {secret_id}")
        except GoogleAPIError as e:
            # Secret may already exist - that's fine, we'll add a version
            if "Already exists" in str(e):
                logger.info(f"Secret {secret_id} already exists, adding new version")
            else:
                raise
        
        # Add a new version of the secret
        response = client.add_secret_version(
            request={
                "parent": secret_path,
                "payload": {"data": secret_value.encode("UTF-8")}
            }
        )
        
        return json.dumps({
            "success": True,
            "secret_id": secret_id,
            "version_name": response.name,
            "message": f"Secret '{secret_id}' updated successfully"
        })
    
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied writing secret '{secret_id}'. Ensure service account has roles/secretmanager.admin"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error writing secret {secret_id}")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


def list_secrets(project_id: Optional[str] = None, limit: int = 50) -> str:
    """List all secrets in a GCP project.
    
    Args:
        project_id: GCP project ID (optional, uses env var if not provided)
        limit: Maximum number of secrets to return (default: 50)
    
    Returns:
        JSON string with list of secrets or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Secret Manager client not installed. Run: pip install google-cloud-secret-manager"})
    
    # Determine project ID
    if not project_id:
        project_id = (
            os.getenv("GOOGLE_CLOUD_PROJECT") or
            os.getenv("GCP_PROJECT") or
            os.getenv("GCLOUD_PROJECT")
        )
    
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    try:
        client = secretmanager.SecretManagerServiceClient()
        parent = f"projects/{project_id}"
        
        secrets = []
        count = 0
        for secret in client.list_secrets(request={"parent": parent}):
            if count >= limit:
                break
            secrets.append({
                "name": secret.name.split("/")[-1],
                "create_time": str(secret.create_time) if secret.create_time else None,
                "replication": "automatic" if secret.replication.automatic else "custom"
            })
            count += 1
        
        return json.dumps({
            "success": True,
            "project_id": project_id,
            "count": len(secrets),
            "secrets": secrets
        })
    
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied listing secrets in project '{project_id}'. Ensure service account has roles/secretmanager.viewer or higher"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error listing secrets")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


# --- Schemas ---
READ_SECRET_SCHEMA = {
    "name": "gcp_secret_read",
    "description": "Read a secret from Google Cloud Secret Manager. Use this to retrieve API keys, credentials, or other sensitive values securely stored in GCP.",
    "parameters": {
        "type": "object",
        "properties": {
            "secret_id": {
                "type": "string",
                "description": "The ID of the secret to read (e.g., 'ollama-api-key', 'database-password')"
            },
            "version_id": {
                "type": "string",
                "description": "Version ID or 'latest' (default: 'latest')",
                "default": "latest"
            },
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            }
        },
        "required": ["secret_id"]
    }
}

WRITE_SECRET_SCHEMA = {
    "name": "gcp_secret_write",
    "description": "Create or update a secret in Google Cloud Secret Manager. Use this to store API keys, credentials, or other sensitive values securely. Requires roles/secretmanager.admin permission.",
    "parameters": {
        "type": "object",
        "properties": {
            "secret_id": {
                "type": "string",
                "description": "The ID of the secret to create/update (e.g., 'ollama-api-key', 'database-password')"
            },
            "secret_value": {
                "type": "string",
                "description": "The secret value to store (will be encrypted by GCP)"
            },
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            }
        },
        "required": ["secret_id", "secret_value"]
    }
}

LIST_SECRETS_SCHEMA = {
    "name": "gcp_secret_list",
    "description": "List all secrets in a Google Cloud project. Returns secret names and metadata (not values). Use this to discover available secrets.",
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of secrets to return (default: 50)",
                "default": 50
            }
        },
        "required": []
    }
}


# --- Registration ---
from tools.registry import registry

registry.register(
    name="gcp_secret_read",
    toolset="gcp-secret-manager",
    schema=READ_SECRET_SCHEMA,
    handler=lambda args, **kw: read_secret(
        secret_id=args.get("secret_id", ""),
        version_id=args.get("version_id", "latest"),
        project_id=args.get("project_id")),
    check_fn=check_gcp_secret_manager_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],  # Or GCP_PROJECT, GCLOUD_PROJECT
)

registry.register(
    name="gcp_secret_write",
    toolset="gcp-secret-manager",
    schema=WRITE_SECRET_SCHEMA,
    handler=lambda args, **kw: write_secret(
        secret_id=args.get("secret_id", ""),
        secret_value=args.get("secret_value", ""),
        project_id=args.get("project_id")),
    check_fn=check_gcp_secret_manager_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)

registry.register(
    name="gcp_secret_list",
    toolset="gcp-secret-manager",
    schema=LIST_SECRETS_SCHEMA,
    handler=lambda args, **kw: list_secrets(
        project_id=args.get("project_id"),
        limit=args.get("limit", 50)),
    check_fn=check_gcp_secret_manager_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)
