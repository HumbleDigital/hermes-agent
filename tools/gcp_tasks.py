# tools/gcp_tasks.py
"""Google Cloud Tasks integration for Hermes Agent.

Provides inter-agent communication via Google Tasks queues.
Use this for:
- Creating work queues for agent tasks
- Inter-agent messaging and coordination
- Priority-based task scheduling (P1/P2/P3)
- Audit trail via task metadata

Requires:
- Google Cloud project with Cloud Tasks API enabled
- Service account with roles/cloudtasks.admin or roles/cloudtasks.taskCreator
- Application Default Credentials (ADC) or GOOGLE_APPLICATION_CREDENTIALS env var
- Queue name (format: projects/PROJECT_ID/locations/LOCATION/queues/QUEUE_NAME)

Architecture:
- Default location: us-east1 (NY proximity per cost target)
- Queue naming: hermes-<agent-name>-queue (e.g., hermes-ops-queue)
- Task payload: JSON with priority, task_type, metadata, and instructions
- FIFO ordering within priority levels
"""
import json
import os
import logging
import base64
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

try:
    from google.cloud import tasks_v2
    from google.api_core.exceptions import NotFound, PermissionDenied, GoogleAPIError
    HAS_GCP = True
except ImportError:
    HAS_GCP = False
    tasks_v2 = None  # Placeholder for type hints
    logger.warning("Google Cloud Tasks client not installed. Tool will be unavailable.")


def check_gcp_tasks_requirements() -> bool:
    """Return True if GCP Tasks dependencies and credentials are available."""
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
    """Create and return a Cloud Tasks client."""
    if not HAS_GCP:
        return None
    
    try:
        client = tasks_v2.CloudTasksClient()
        return client
    except Exception as e:
        logger.error(f"Failed to create Cloud Tasks client: {e}")
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
    return os.getenv("GCP_TASKS_LOCATION", "us-east1")


def create_task(
    queue_name: str,
    task_payload: Dict[str, Any],
    task_name: Optional[str] = None,
    priority: str = "P2",
    schedule_time: Optional[datetime] = None,
    project_id: Optional[str] = None,
    location: Optional[str] = None
) -> str:
    """Create a new task in a Cloud Tasks queue.
    
    Args:
        queue_name: Queue name (e.g., "hermes-ops-queue" or full path)
        task_payload: JSON-serializable dict with task data
        task_name: Optional task name (auto-generated if not provided)
        priority: P1 (high), P2 (normal), P3 (low) - affects routing, not Cloud Tasks priority
        schedule_time: Optional datetime to schedule task for future execution
        project_id: GCP project ID (optional, uses env var if not provided)
        location: GCP location (optional, defaults to us-east1)
    
    Returns:
        JSON string with task name or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Tasks client not installed. Run: pip install google-cloud-tasks"})
    
    project_id = _get_project_id(project_id)
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    location = location or _get_default_location()
    
    # Build full queue path if only queue name provided
    if "/" not in queue_name:
        queue_path = f"projects/{project_id}/locations/{location}/queues/{queue_name}"
    else:
        queue_path = queue_name
    
    try:
        client = _get_client()
        if not client:
            return json.dumps({"error": "Failed to create Cloud Tasks client"})
        
        # Serialize payload
        payload_json = json.dumps({
            "priority": priority,
            "created_at": datetime.utcnow().isoformat(),
            "task_name": task_name or f"task-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            **task_payload
        })
        
        # Create task
        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": os.getenv("GCP_TASKS_HANDLER_URL", "https://your-handler-url.com/tasks"),  # Configurable
                "headers": {
                    "Content-Type": "application/json",
                    "X-Task-Priority": priority,
                },
                "body": payload_json.encode("utf-8"),
            },
        }
        
        # Set schedule time if provided
        if schedule_time:
            task["schedule_time"] = tasks_v2.Timestamp()
            task["schedule_time"].FromDatetime(schedule_time)
        
        # Set custom name if provided
        if task_name:
            task["name"] = f"{queue_path}/tasks/{task_name}"
        
        response = client.create_task(request={"parent": queue_path, "task": task})
        
        logger.info(f"Created task: {response.name}")
        
        return json.dumps({
            "success": True,
            "task_name": response.name.split("/")[-1],
            "task_path": response.name,
            "queue": queue_path,
            "priority": priority,
            "scheduled_for": schedule_time.isoformat() if schedule_time else "immediate",
        })
    
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied creating task in queue '{queue_name}'. Ensure service account has roles/cloudtasks.taskCreator"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error creating task in queue {queue_name}")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


def list_tasks(
    queue_name: str,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    limit: int = 50
) -> str:
    """List tasks in a Cloud Tasks queue.
    
    Args:
        queue_name: Queue name (e.g., "hermes-ops-queue")
        project_id: GCP project ID (optional, uses env var if not provided)
        location: GCP location (optional, defaults to us-east1)
        limit: Maximum number of tasks to return (default: 50)
    
    Returns:
        JSON string with list of tasks or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Tasks client not installed. Run: pip install google-cloud-tasks"})
    
    project_id = _get_project_id(project_id)
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    location = location or _get_default_location()
    
    # Build full queue path
    if "/" not in queue_name:
        queue_path = f"projects/{project_id}/locations/{location}/queues/{queue_name}"
    else:
        queue_path = queue_name
    
    try:
        client = _get_client()
        if not client:
            return json.dumps({"error": "Failed to create Cloud Tasks client"})
        
        tasks = []
        count = 0
        
        for task in client.list_tasks(request={"parent": queue_path}):
            if count >= limit:
                break
            
            # Decode payload if present
            payload = {}
            if task.http_request and task.http_request.body:
                try:
                    payload = json.loads(task.http_request.body.decode("utf-8"))
                except:
                    payload = {"raw": True}
            
            tasks.append({
                "name": task.name.split("/")[-1],
                "path": task.name,
                "priority": payload.get("priority", "unknown"),
                "task_name": payload.get("task_name", "unknown"),
                "created_at": payload.get("created_at", "unknown"),
                "schedule_time": task.schedule_time.isoformat() if task.schedule_time else "immediate",
                "state": "QUEUED",  # Cloud Tasks doesn't expose state in list
            })
            count += 1
        
        return json.dumps({
            "success": True,
            "queue": queue_path,
            "count": len(tasks),
            "tasks": tasks
        })
    
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied listing tasks in queue '{queue_name}'"})
    except NotFound:
        return json.dumps({"error": f"Queue '{queue_name}' not found in project '{project_id}'"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error listing tasks in queue {queue_name}")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


def delete_task(
    queue_name: str,
    task_name: str,
    project_id: Optional[str] = None,
    location: Optional[str] = None
) -> str:
    """Delete a task from a Cloud Tasks queue.
    
    Args:
        queue_name: Queue name (e.g., "hermes-ops-queue")
        task_name: Task name to delete
        project_id: GCP project ID (optional, uses env var if not provided)
        location: GCP location (optional, defaults to us-east1)
    
    Returns:
        JSON string with success status or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Tasks client not installed. Run: pip install google-cloud-tasks"})
    
    project_id = _get_project_id(project_id)
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    location = location or _get_default_location()
    
    # Build full paths
    if "/" not in queue_name:
        queue_path = f"projects/{project_id}/locations/{location}/queues/{queue_name}"
    else:
        queue_path = queue_name
    
    task_path = f"{queue_path}/tasks/{task_name}"
    
    try:
        client = _get_client()
        if not client:
            return json.dumps({"error": "Failed to create Cloud Tasks client"})
        
        client.delete_task(request={"name": task_path})
        
        logger.info(f"Deleted task: {task_name} from queue {queue_name}")
        
        return json.dumps({
            "success": True,
            "task_name": task_name,
            "queue": queue_path,
            "message": f"Task '{task_name}' deleted successfully"
        })
    
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied deleting task '{task_name}'"})
    except NotFound:
        return json.dumps({"error": f"Task '{task_name}' not found in queue '{queue_name}'"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error deleting task {task_name}")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


def create_queue(
    queue_name: str,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    rate_limit: Optional[float] = None,
    max_concurrent: Optional[int] = None
) -> str:
    """Create a new Cloud Tasks queue.
    
    Args:
        queue_name: Queue name (e.g., "hermes-ops-queue")
        project_id: GCP project ID (optional, uses env var if not provided)
        location: GCP location (optional, defaults to us-east1)
        rate_limit: Optional max dispatches per second
        max_concurrent: Optional max concurrent dispatches
    
    Returns:
        JSON string with queue name or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Tasks client not installed. Run: pip install google-cloud-tasks"})
    
    project_id = _get_project_id(project_id)
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    location = location or _get_default_location()
    
    try:
        client = _get_client()
        if not client:
            return json.dumps({"error": "Failed to create Cloud Tasks client"})
        
        parent = f"projects/{project_id}/locations/{location}"
        queue_path = f"{parent}/queues/{queue_name}"
        
        # Build queue config
        queue = {"name": queue_path}
        
        # Add rate limits if provided
        if rate_limit or max_concurrent:
            queue["rate_limits"] = {}
            if rate_limit:
                queue["rate_limits"]["max_dispatches_per_second"] = rate_limit
            if max_concurrent:
                queue["rate_limits"]["max_concurrent_dispatches"] = max_concurrent
        
        # Try to create
        try:
            response = client.create_queue(request={"parent": parent, "queue": queue})
            logger.info(f"Created queue: {response.name}")
        except GoogleAPIError as e:
            if "Already exists" in str(e):
                # Queue exists, update it instead
                response = client.update_queue(request={"queue": queue})
                logger.info(f"Updated existing queue: {response.name}")
            else:
                raise
        
        return json.dumps({
            "success": True,
            "queue_name": queue_name,
            "queue_path": response.name,
            "location": location,
            "rate_limit": rate_limit,
            "max_concurrent": max_concurrent,
        })
    
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied creating queue '{queue_name}'. Ensure service account has roles/cloudtasks.admin"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error creating queue {queue_name}")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


def list_queues(
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    limit: int = 50
) -> str:
    """List Cloud Tasks queues in a project/location.
    
    Args:
        project_id: GCP project ID (optional, uses env var if not provided)
        location: GCP location (optional, defaults to us-east1)
        limit: Maximum number of queues to return (default: 50)
    
    Returns:
        JSON string with list of queues or error
    """
    if not HAS_GCP:
        return json.dumps({"error": "Google Cloud Tasks client not installed. Run: pip install google-cloud-tasks"})
    
    project_id = _get_project_id(project_id)
    if not project_id:
        return json.dumps({"error": "No project ID provided. Set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, or GCLOUD_PROJECT environment variable."})
    
    location = location or _get_default_location()
    
    try:
        client = _get_client()
        if not client:
            return json.dumps({"error": "Failed to create Cloud Tasks client"})
        
        parent = f"projects/{project_id}/locations/{location}"
        queues = []
        count = 0
        
        for queue in client.list_queues(request={"parent": parent}):
            if count >= limit:
                break
            
            rate_limits = queue.rate_limits if hasattr(queue, 'rate_limits') and queue.rate_limits else None
            
            queues.append({
                "name": queue.name.split("/")[-1],
                "path": queue.name,
                "location": location,
                "rate_limit": rate_limits.max_dispatches_per_second if rate_limits else None,
                "max_concurrent": rate_limits.max_concurrent_dispatches if rate_limits else None,
            })
            count += 1
        
        return json.dumps({
            "success": True,
            "location": location,
            "count": len(queues),
            "queues": queues
        })
    
    except PermissionDenied:
        return json.dumps({"error": f"Permission denied listing queues in project '{project_id}'"})
    except GoogleAPIError as e:
        return json.dumps({"error": f"Google Cloud API error: {str(e)}"})
    except Exception as e:
        logger.exception(f"Unexpected error listing queues")
        return json.dumps({"error": f"Unexpected error: {str(e)}"})


# --- Schemas ---

CREATE_TASK_SCHEMA = {
    "name": "gcp_tasks_create",
    "description": "Create a new task in a Google Cloud Tasks queue. Use this for inter-agent communication, work queues, and priority-based task scheduling. Tasks are dispatched to a configured HTTP handler URL.",
    "parameters": {
        "type": "object",
        "properties": {
            "queue_name": {
                "type": "string",
                "description": "Queue name (e.g., 'hermes-ops-queue') or full path"
            },
            "task_payload": {
                "type": "object",
                "description": "JSON-serializable dict with task data (instructions, metadata, etc.)"
            },
            "task_name": {
                "type": "string",
                "description": "Optional task name (auto-generated if not provided)"
            },
            "priority": {
                "type": "string",
                "description": "Priority level: P1 (high), P2 (normal), P3 (low)",
                "enum": ["P1", "P2", "P3"],
                "default": "P2"
            },
            "schedule_time": {
                "type": "string",
                "description": "Optional ISO 8601 datetime to schedule task for future execution (e.g., '2026-04-17T10:00:00Z')"
            },
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            },
            "location": {
                "type": "string",
                "description": "GCP location (optional, defaults to us-east1)",
                "default": "us-east1"
            }
        },
        "required": ["queue_name", "task_payload"]
    }
}

LIST_TASKS_SCHEMA = {
    "name": "gcp_tasks_list",
    "description": "List tasks in a Google Cloud Tasks queue. Returns task names, priorities, and metadata (not full payloads). Use this to inspect queue state.",
    "parameters": {
        "type": "object",
        "properties": {
            "queue_name": {
                "type": "string",
                "description": "Queue name (e.g., 'hermes-ops-queue')"
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
            "limit": {
                "type": "integer",
                "description": "Maximum number of tasks to return (default: 50)",
                "default": 50
            }
        },
        "required": ["queue_name"]
    }
}

DELETE_TASK_SCHEMA = {
    "name": "gcp_tasks_delete",
    "description": "Delete a task from a Google Cloud Tasks queue. Use this to remove completed or cancelled tasks.",
    "parameters": {
        "type": "object",
        "properties": {
            "queue_name": {
                "type": "string",
                "description": "Queue name (e.g., 'hermes-ops-queue')"
            },
            "task_name": {
                "type": "string",
                "description": "Task name to delete"
            },
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            },
            "location": {
                "type": "string",
                "description": "GCP location (optional, defaults to us-east1)",
                "default": "us-east1"
            }
        },
        "required": ["queue_name", "task_name"]
    }
}

CREATE_QUEUE_SCHEMA = {
    "name": "gcp_tasks_create_queue",
    "description": "Create a new Google Cloud Tasks queue. Use this to set up dedicated queues for agents or workflows. Requires roles/cloudtasks.admin permission.",
    "parameters": {
        "type": "object",
        "properties": {
            "queue_name": {
                "type": "string",
                "description": "Queue name (e.g., 'hermes-ops-queue')"
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
            "rate_limit": {
                "type": "number",
                "description": "Optional max dispatches per second"
            },
            "max_concurrent": {
                "type": "integer",
                "description": "Optional max concurrent dispatches"
            }
        },
        "required": ["queue_name"]
    }
}

LIST_QUEUES_SCHEMA = {
    "name": "gcp_tasks_list_queues",
    "description": "List Cloud Tasks queues in a project/location. Returns queue names and rate limit config. Use this to discover available queues.",
    "parameters": {
        "type": "object",
        "properties": {
            "project_id": {
                "type": "string",
                "description": "GCP project ID (optional, uses GOOGLE_CLOUD_PROJECT env var if not provided)"
            },
            "location": {
                "type": "string",
                "description": "GCP location (optional, defaults to us-east1)",
                "default": "us-east1"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of queues to return (default: 50)",
                "default": 50
            }
        },
        "required": []
    }
}


# --- Registration ---
from tools.registry import registry

registry.register(
    name="gcp_tasks_create",
    toolset="gcp-tasks",
    schema=CREATE_TASK_SCHEMA,
    handler=lambda args, **kw: create_task(
        queue_name=args.get("queue_name", ""),
        task_payload=args.get("task_payload", {}),
        task_name=args.get("task_name"),
        priority=args.get("priority", "P2"),
        schedule_time=datetime.fromisoformat(args["schedule_time"].replace("Z", "+00:00")) if args.get("schedule_time") else None,
        project_id=args.get("project_id"),
        location=args.get("location"),
    ),
    check_fn=check_gcp_tasks_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)

registry.register(
    name="gcp_tasks_list",
    toolset="gcp-tasks",
    schema=LIST_TASKS_SCHEMA,
    handler=lambda args, **kw: list_tasks(
        queue_name=args.get("queue_name", ""),
        project_id=args.get("project_id"),
        location=args.get("location"),
        limit=args.get("limit", 50),
    ),
    check_fn=check_gcp_tasks_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)

registry.register(
    name="gcp_tasks_delete",
    toolset="gcp-tasks",
    schema=DELETE_TASK_SCHEMA,
    handler=lambda args, **kw: delete_task(
        queue_name=args.get("queue_name", ""),
        task_name=args.get("task_name", ""),
        project_id=args.get("project_id"),
        location=args.get("location"),
    ),
    check_fn=check_gcp_tasks_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)

registry.register(
    name="gcp_tasks_create_queue",
    toolset="gcp-tasks",
    schema=CREATE_QUEUE_SCHEMA,
    handler=lambda args, **kw: create_queue(
        queue_name=args.get("queue_name", ""),
        project_id=args.get("project_id"),
        location=args.get("location"),
        rate_limit=args.get("rate_limit"),
        max_concurrent=args.get("max_concurrent"),
    ),
    check_fn=check_gcp_tasks_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)

registry.register(
    name="gcp_tasks_list_queues",
    toolset="gcp-tasks",
    schema=LIST_QUEUES_SCHEMA,
    handler=lambda args, **kw: list_queues(
        project_id=args.get("project_id"),
        location=args.get("location"),
        limit=args.get("limit", 50),
    ),
    check_fn=check_gcp_tasks_requirements,
    requires_env=["GOOGLE_CLOUD_PROJECT"],
)
