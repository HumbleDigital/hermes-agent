# tools/gcp_storage.py
"""Google Cloud Storage integration for Hermes Agent.

Provides Cloud Storage operations for:
- Per-client data buckets (isolated storage)
- Volume mounting for agent deployments
- File upload/download for agent workflows
- Bucket lifecycle management

Requires:
- Google Cloud project with Cloud Storage API enabled
- Service account with roles/storage.admin or roles/storage.objectAdmin
- Application Default Credentials (ADC) or GOOGLE_APPLICATION_CREDENTIALS env var

Architecture:
- Bucket naming: hd-<client-id>-data (e.g., hd-acme-corp-data)
- Default location: us-east1 (NY proximity per cost target)
- Storage class: STANDARD for active data, NEARLINE for archives
- Uniform bucket-level access enabled (IAM-based permissions)
"""
import json
import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from google.cloud import storage
    from google.api_core.exceptions import NotFound, PermissionDenied, GoogleAPIError, Conflict
    HAS_GCP = True
except ImportError:
    HAS_GCP = False
    storage = None  # Placeholder for type hints
    logger.warning("Google Cloud Storage client not installed. Tool will be unavailable.")


def check_gcp_storage_requirements() -> bool:
    """Return True if GCP Storage dependencies and credentials are available."""
    if not HAS_GCP:
        return False
    
    # Check for ADC or explicit credentials
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    adc_path = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    
    has_credentials = (
        (credentials_path and os.path.exists(credentials_path)) or
        os.path.exists(adc_path)
    )
    
    has_project = bool(
        os.getenv("GOOGLE_CLOUD_PROJECT") or
        os.getenv("GCP_PROJECT") or
        os.getenv("GCLOUD_PROJECT")
    )
    
    return has_credentials and has_project


def _get_client():
    """Create and return a Cloud Storage client."""
    if not HAS_GCP:
        return None
    
    try:
        client = storage.Client()
        return client
    except Exception as e:
        logger.error(f"Failed to create GCS client: {e}")
        return None


def _get_project_id(provided_project_id: Optional[str] = None) -> Optional[str]:
    """Resolve project ID from parameter or environment."""
    if provided_project_id:
        return provided_project_id
    
    return (
        os.getenv("GOOGLE_CLOUD_PROJECT") or
        os.getenv("GCP_PROJECT") or
        os.getenv("GCLOUD_PROJECT")
    )


def _get_default_location() -> str:
    """Get default location from env or use us-east1."""
    return os.getenv("GCP_STORAGE_LOCATION", "us-east1")


def create_bucket(
    bucket_name: str,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    storage_class: str = "STANDARD",
    uniform_access: bool = True,
    lifecycle_rules: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Create a new Cloud Storage bucket.
    
    Args:
        bucket_name: Bucket name (must be globally unique)
        project_id: GCP project ID (optional, uses env var if not provided)
        location: GCP location (optional, defaults to us-east1)
        storage_class: STANDARD, NEARLINE, COLDLINE, or ARCHIVE
        uniform_access: Enable uniform bucket-level access (recommended)
        lifecycle_rules: Optional lifecycle rules for object management
    
    Returns:
        JSON string with bucket info or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Storage client not installed. Run: pip install google-cloud-storage"})
    
    project_id = _get_project_id(project_id)
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    location = location or _get_default_location()
    
    try:
        client = _get_client()
        if not client:
            return json.dumps({"error": "Failed to create GCS client"})
        
        bucket = client.bucket(bucket_name)
        
        # Configure bucket
        bucket.location = location
        bucket.storage_class = storage_class
        bucket.uniform_bucket_level_access = uniform_access
        
        # Add lifecycle rules if provided
        if lifecycle_rules:
            bucket.lifecycle_rules = lifecycle_rules
        
        # Create bucket
        bucket = client.create_bucket(
            bucket,
            project=project_id,
            predefined_acl="private",  # Default to private
        )
        
        logger.info(f"Created bucket: {bucket.name} in {location}")
        
        return json.dumps({
            "success": True,
            "bucket_name": bucket.name,
            "location": bucket.location,
            "storage_class": bucket.storage_class,
            "uniform_access": bucket.uniform_bucket_level_access,
            "created": bucket.time_created.isoformat() if bucket.time_created else None,
        })
    
    except Conflict:
        return json.dumps({"error": f"Bucket '{bucket_name}' already exists. Bucket names must be globally unique."})
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied creating bucket '{bucket_name}'. Ensure service account has roles/storage.admin"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error creating bucket {bucket_name}")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


def list_buckets(project_id: Optional[str] = None, prefix: Optional[str] = None, limit: int = 50) -> str:
    """List Cloud Storage buckets in a project.
    
    Args:
        project_id: GCP project ID (optional, uses env var if not provided)
        prefix: Optional prefix to filter bucket names
        limit: Maximum number of buckets to return (default: 50)
    
    Returns:
        JSON string with list of buckets or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Storage client not installed. Run: pip install google-cloud-storage"})
    
    project_id = _get_project_id(project_id)
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    try:
        client = _get_client()
        if not client:
            return json.dumps({"error": "Failed to create GCS client"})
        
        buckets = []
        count = 0
        
        for bucket in client.list_buckets(project=project_id):
            if prefix and not bucket.name.startswith(prefix):
                continue
            
            if count >= limit:
                break
            
            # Get IAM policy to check uniform bucket-level access
            uniform_access = False
            try:
                policy = bucket.get_iam_policy()
                # If uniform bucket-level access is enabled, the policy will be returned
                uniform_access = True
            except Exception:
                uniform_access = False
            
            buckets.append({
                "name": bucket.name,
                "location": bucket.location,
                "storage_class": bucket.storage_class,
                "uniform_access": uniform_access,
                "created": bucket.time_created.isoformat() if bucket.time_created else None,
            })
            count += 1
        
        return json.dumps({
            "success": True,
            "project_id": project_id,
            "count": len(buckets),
            "buckets": buckets
        })
    
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied listing buckets in project '{project_id}'"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error listing buckets")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


def delete_bucket(bucket_name: str, force: bool = False, project_id: Optional[str] = None) -> str:
    """Delete a Cloud Storage bucket.
    
    Args:
        bucket_name: Bucket name to delete
        force: If True, delete all objects first (default: False)
        project_id: GCP project ID (optional, uses env var if not provided)
    
    Returns:
        JSON string with success status or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Storage client not installed. Run: pip install google-cloud-storage"})
    
    project_id = _get_project_id(project_id)
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    try:
        client = _get_client()
        if not client:
            return json.dumps({"error": "Failed to create GCS client"})
        
        bucket = client.bucket(bucket_name)
        
        # Force delete all objects first
        if force:
            blobs = bucket.list_blobs()
            for blob in blobs:
                blob.delete()
            logger.info(f"Deleted all objects in bucket {bucket_name}")
        
        bucket.delete()
        
        logger.info(f"Deleted bucket: {bucket_name}")
        
        return json.dumps({
            "success": True,
            "bucket_name": bucket_name,
            "message": f"Bucket '{bucket_name}' deleted successfully"
        })
    
    except NotFound:
        return json.dumps({"error": f"Bucket '{bucket_name}' not found"})
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied deleting bucket '{bucket_name}'"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error deleting bucket {bucket_name}")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


def upload_file(
    bucket_name: str,
    source_path: str,
    destination_blob: Optional[str] = None,
    project_id: Optional[str] = None
) -> str:
    """Upload a file to Cloud Storage.
    
    Args:
        bucket_name: Target bucket name
        source_path: Local file path to upload
        destination_blob: Optional blob name (defaults to filename)
        project_id: GCP project ID (optional, uses env var if not provided)
    
    Returns:
        JSON string with upload info or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Storage client not installed. Run: pip install google-cloud-storage"})
    
    project_id = _get_project_id(project_id)
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    # Resolve source path
    source_path = os.path.expanduser(source_path)
    if not os.path.exists(source_path):
        return json.dumps({"error": f"File not found: {source_path}"})
    
    # Default blob name to filename
    if not destination_blob:
        destination_blob = os.path.basename(source_path)
    
    try:
        client = _get_client()
        if not client:
            return json.dumps({"error": "Failed to create GCS client"})
        
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(destination_blob)
        
        # Upload
        blob.upload_from_filename(source_path)
        
        logger.info(f"Uploaded {source_path} to gs://{bucket_name}/{destination_blob}")
        
        return json.dumps({
            "success": True,
            "bucket": bucket_name,
            "blob": destination_blob,
            "source": source_path,
            "size": blob.size,
            "updated": blob.updated.isoformat() if blob.updated else None,
            "media_link": blob.media_link,
        })
    
    except NotFound:
        return json.dumps({"error": f"Bucket '{bucket_name}' not found"})
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied uploading to bucket '{bucket_name}'"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error uploading {source_path}")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


def download_file(
    bucket_name: str,
    blob_name: str,
    destination_path: str,
    project_id: Optional[str] = None
) -> str:
    """Download a file from Cloud Storage.
    
    Args:
        bucket_name: Source bucket name
        blob_name: Blob name to download
        destination_path: Local file path to save to
        project_id: GCP project ID (optional, uses env var if not provided)
    
    Returns:
        JSON string with download info or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Storage client not installed. Run: pip install google-cloud-storage"})
    
    project_id = _get_project_id(project_id)
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    # Resolve destination path
    destination_path = os.path.expanduser(destination_path)
    destination_dir = os.path.dirname(destination_path)
    if destination_dir and not os.path.exists(destination_dir):
        os.makedirs(destination_dir, exist_ok=True)
    
    try:
        client = _get_client()
        if not client:
            return json.dumps({"error": "Failed to create GCS client"})
        
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        # Download
        blob.download_to_filename(destination_path)
        
        logger.info(f"Downloaded gs://{bucket_name}/{blob_name} to {destination_path}")
        
        return json.dumps({
            "success": True,
            "bucket": bucket_name,
            "blob": blob_name,
            "destination": destination_path,
            "size": blob.size,
            "updated": blob.updated.isoformat() if blob.updated else None,
        })
    
    except NotFound:
        return json.dumps({"error": f"Blob '{blob_name}' not found in bucket '{bucket_name}'"})
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied downloading from bucket '{bucket_name}'"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error downloading {blob_name}")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


def list_blobs(
    bucket_name: str,
    prefix: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 100
) -> str:
    """List objects (blobs) in a Cloud Storage bucket.
    
    Args:
        bucket_name: Bucket name
        prefix: Optional prefix to filter blob names
        project_id: GCP project ID (optional, uses env var if not provided)
        limit: Maximum number of blobs to return (default: 100)
    
    Returns:
        JSON string with list of blobs or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Storage client not installed. Run: pip install google-cloud-storage"})
    
    project_id = _get_project_id(project_id)
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    try:
        client = _get_client()
        if not client:
            return json.dumps({"error": "Failed to create GCS client"})
        
        bucket = client.bucket(bucket_name)
        
        blobs = []
        count = 0
        
        for blob in bucket.list_blobs(prefix=prefix):
            if count >= limit:
                break
            
            blobs.append({
                "name": blob.name,
                "size": blob.size,
                "content_type": blob.content_type,
                "updated": blob.updated.isoformat() if blob.updated else None,
                "md5_hash": blob.md5_hash,
            })
            count += 1
        
        return json.dumps({
            "success": True,
            "bucket": bucket_name,
            "prefix": prefix,
            "count": len(blobs),
            "blobs": blobs
        })
    
    except NotFound:
        return json.dumps({"error": f"Bucket '{bucket_name}' not found"})
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied listing blobs in bucket '{bucket_name}'"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error listing blobs in {bucket_name}")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


def delete_blob(
    bucket_name: str,
    blob_name: str,
    project_id: Optional[str] = None
) -> str:
    """Delete an object (blob) from Cloud Storage.
    
    Args:
        bucket_name: Bucket name
        blob_name: Blob name to delete
        project_id: GCP project ID (optional, uses env var if not provided)
    
    Returns:
        JSON string with success status or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Storage client not installed. Run: pip install google-cloud-storage"})
    
    project_id = _get_project_id(project_id)
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    try:
        client = _get_client()
        if not client:
            return json.dumps({"error": "Failed to create GCS client"})
        
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        blob.delete()
        
        logger.info(f"Deleted blob: {blob_name} from bucket {bucket_name}")
        
        return json.dumps({
            "success": True,
            "bucket": bucket_name,
            "blob": blob_name,
            "message": f"Blob '{blob_name}' deleted successfully"
        })
    
    except NotFound:
        return json.dumps({"error": f"Blob '{blob_name}' not found in bucket '{bucket_name}'"})
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied deleting from bucket '{bucket_name}'"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error deleting blob {blob_name}")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


# --- Schemas ---

CREATE_BUCKET_SCHEMA = {
    "name": "gcp_storage_create_bucket",
    "description": "Create a new Google Cloud Storage bucket. Use this for per-client data isolation or agent volume mounting. Bucket names must be globally unique.",
    "parameters": {
        "type": "object",
        "properties": {
            "bucket_name": {
                "type": "string",
                "description": "Bucket name (must be globally unique, e.g., 'hd-acme-corp-data')"
            },
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            },
            "location": {
                "type": "string",
                "description": "GCP location (optional, defaults to us-east1)",
                "default": "us-east1"
            },
            "storage_class": {
                "type": "string",
                "description": "Storage class: STANDARD, NEARLINE, COLDLINE, or ARCHIVE",
                "enum": ["STANDARD", "NEARLINE", "COLDLINE", "ARCHIVE"],
                "default": "STANDARD"
            },
            "uniform_access": {
                "type": "boolean",
                "description": "Enable uniform bucket-level access (recommended, default: true)",
                "default": True
            },
            "lifecycle_rules": {
                "type": "array",
                "description": "Optional lifecycle rules for object management (advanced)"
            }
        },
        "required": ["bucket_name"]
    }
}

LIST_BUCKETS_SCHEMA = {
    "name": "gcp_storage_list_buckets",
    "description": "List Cloud Storage buckets in a project. Returns bucket names, locations, and storage classes.",
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            },
            "prefix": {
                "type": "string",
                "description": "Optional prefix to filter bucket names (e.g., 'hd-' for HumbleDigital buckets)"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of buckets to return (default: 50)",
                "default": 50
            }
        },
        "required": []
    }
}

DELETE_BUCKET_SCHEMA = {
    "name": "gcp_storage_delete_bucket",
    "description": "Delete a Cloud Storage bucket. Use force=true to delete all objects first (caution: irreversible).",
    "parameters": {
        "type": "object",
        "properties": {
            "bucket_name": {
                "type": "string",
                "description": "Bucket name to delete"
            },
            "force": {
                "type": "boolean",
                "description": "If true, delete all objects in bucket first (default: false)",
                "default": False
            },
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            }
        },
        "required": ["bucket_name"]
    }
}

UPLOAD_FILE_SCHEMA = {
    "name": "gcp_storage_upload",
    "description": "Upload a file to Cloud Storage. Use this to store agent outputs, backups, or client data in GCS buckets.",
    "parameters": {
        "type": "object",
        "properties": {
            "bucket_name": {
                "type": "string",
                "description": "Target bucket name"
            },
            "source_path": {
                "type": "string",
                "description": "Local file path to upload (e.g., '/path/to/file.txt')"
            },
            "destination_blob": {
                "type": "string",
                "description": "Optional blob name in bucket (defaults to filename)"
            },
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            }
        },
        "required": ["bucket_name", "source_path"]
    }
}

DOWNLOAD_FILE_SCHEMA = {
    "name": "gcp_storage_download",
    "description": "Download a file from Cloud Storage. Use this to retrieve agent inputs, configs, or client data from GCS buckets.",
    "parameters": {
        "type": "object",
        "properties": {
            "bucket_name": {
                "type": "string",
                "description": "Source bucket name"
            },
            "blob_name": {
                "type": "string",
                "description": "Blob name to download (e.g., 'path/to/file.txt')"
            },
            "destination_path": {
                "type": "string",
                "description": "Local file path to save to"
            },
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            }
        },
        "required": ["bucket_name", "blob_name", "destination_path"]
    }
}

LIST_BLOBS_SCHEMA = {
    "name": "gcp_storage_list_blobs",
    "description": "List objects (blobs) in a Cloud Storage bucket. Returns blob names, sizes, and metadata.",
    "parameters": {
        "type": "object",
        "properties": {
            "bucket_name": {
                "type": "string",
                "description": "Bucket name"
            },
            "prefix": {
                "type": "string",
                "description": "Optional prefix to filter blob names (e.g., 'logs/' for log files)"
            },
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of blobs to return (default: 100)",
                "default": 100
            }
        },
        "required": ["bucket_name"]
    }
}

DELETE_BLOB_SCHEMA = {
    "name": "gcp_storage_delete_blob",
    "description": "Delete an object (blob) from Cloud Storage. Use this to remove outdated files or clean up temporary data.",
    "parameters": {
        "type": "object",
        "properties": {
            "bucket_name": {
                "type": "string",
                "description": "Bucket name"
            },
            "blob_name": {
                "type": "string",
                "description": "Blob name to delete"
            },
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            }
        },
        "required": ["bucket_name", "blob_name"]
    }
}


# --- Registration ---
from tools.registry import registry

registry.register(
    name="gcp_storage_create_bucket",
    toolset="gcp-storage",
    schema=CREATE_BUCKET_SCHEMA,
    handler=lambda args, **kw: create_bucket(
        bucket_name=args.get("bucket_name", ""),
        project_id=args.get("project_id"),
        location=args.get("location"),
        storage_class=args.get("storage_class", "STANDARD"),
        uniform_access=args.get("uniform_access", True),
        lifecycle_rules=args.get("lifecycle_rules"),
    ),
    check_fn=check_gcp_storage_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)

registry.register(
    name="gcp_storage_list_buckets",
    toolset="gcp-storage",
    schema=LIST_BUCKETS_SCHEMA,
    handler=lambda args, **kw: list_buckets(
        project_id=args.get("project_id"),
        prefix=args.get("prefix"),
        limit=args.get("limit", 50),
    ),
    check_fn=check_gcp_storage_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)

registry.register(
    name="gcp_storage_delete_bucket",
    toolset="gcp-storage",
    schema=DELETE_BUCKET_SCHEMA,
    handler=lambda args, **kw: delete_bucket(
        bucket_name=args.get("bucket_name", ""),
        force=args.get("force", False),
        project_id=args.get("project_id"),
    ),
    check_fn=check_gcp_storage_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)

registry.register(
    name="gcp_storage_upload",
    toolset="gcp-storage",
    schema=UPLOAD_FILE_SCHEMA,
    handler=lambda args, **kw: upload_file(
        bucket_name=args.get("bucket_name", ""),
        source_path=args.get("source_path", ""),
        destination_blob=args.get("destination_blob"),
        project_id=args.get("project_id"),
    ),
    check_fn=check_gcp_storage_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)

registry.register(
    name="gcp_storage_download",
    toolset="gcp-storage",
    schema=DOWNLOAD_FILE_SCHEMA,
    handler=lambda args, **kw: download_file(
        bucket_name=args.get("bucket_name", ""),
        blob_name=args.get("blob_name", ""),
        destination_path=args.get("destination_path", ""),
        project_id=args.get("project_id"),
    ),
    check_fn=check_gcp_storage_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)

registry.register(
    name="gcp_storage_list_blobs",
    toolset="gcp-storage",
    schema=LIST_BLOBS_SCHEMA,
    handler=lambda args, **kw: list_blobs(
        bucket_name=args.get("bucket_name", ""),
        prefix=args.get("prefix"),
        project_id=args.get("project_id"),
        limit=args.get("limit", 100),
    ),
    check_fn=check_gcp_storage_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)

registry.register(
    name="gcp_storage_delete_blob",
    toolset="gcp-storage",
    schema=DELETE_BLOB_SCHEMA,
    handler=lambda args, **kw: delete_blob(
        bucket_name=args.get("bucket_name", ""),
        blob_name=args.get("blob_name", ""),
        project_id=args.get("project_id"),
    ),
    check_fn=check_gcp_storage_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)
