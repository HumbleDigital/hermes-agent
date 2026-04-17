#!/usr/bin/env python3
"""Phase 1 local smoke runner for Google Chat + GCP tools.

This script is intentionally thin: it runs the live checks, parses tool
results as JSON envelopes, and cleans up ephemeral resources on exit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("phase1-smoke")


def _env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _require_env(name: str, errors: list[str]) -> str | None:
    value = os.getenv(name)
    if not value:
        errors.append(f"{name} not set")
    return value


def _json_result(result: Any) -> dict[str, Any]:
    if isinstance(result, str):
        return json.loads(result)
    raise TypeError(f"Expected JSON string result, got {type(result).__name__}")


def _assert_success(name: str, payload: dict[str, Any]) -> None:
    if payload.get("error"):
        raise RuntimeError(f"{name} failed: {payload['error']}")
    if payload.get("success") is not True:
        raise RuntimeError(f"{name} did not report success: {payload}")


@dataclass
class CleanupStack:
    items: list[tuple[str, Callable[[], None]]] = field(default_factory=list)

    def add(self, label: str, fn: Callable[[], None]) -> None:
        self.items.append((label, fn))

    def run(self) -> list[str]:
        failures: list[str] = []
        for label, fn in reversed(self.items):
            try:
                fn()
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                failures.append(f"{label}: {exc}")
        return failures


def check_prerequisites() -> tuple[bool, dict[str, str]]:
    print("=" * 60)
    print("PHASE 1 LOCAL SMOKE RUNNER")
    print("=" * 60)

    errors: list[str] = []
    project = _env("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT", "GCLOUD_PROJECT")
    creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    subject = os.getenv("GOOGLE_CHAT_DWD_SUBJECT")
    space_id = os.getenv("GOOGLE_CHAT_SPACE_ID")
    handler_url = os.getenv("GCP_TASKS_HANDLER_URL")

    if not creds:
        errors.append("GOOGLE_APPLICATION_CREDENTIALS not set")
    elif not Path(creds).expanduser().exists():
        errors.append(f"GOOGLE_APPLICATION_CREDENTIALS does not exist: {creds}")
    if not project:
        errors.append("GOOGLE_CLOUD_PROJECT/GCP_PROJECT/GCLOUD_PROJECT not set")
    if not subject:
        errors.append("GOOGLE_CHAT_DWD_SUBJECT not set")
    if not space_id:
        errors.append("GOOGLE_CHAT_SPACE_ID not set")
    if not handler_url:
        errors.append("GCP_TASKS_HANDLER_URL not set")

    if errors:
        print("Missing prerequisites:")
        for error in errors:
            print(f"  - {error}")
        return False, {}

    summary = {
        "project": project,
        "subject": subject,
        "space_id": space_id,
        "handler_url": handler_url,
    }
    print("Prerequisites:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    return True, summary


def smoke_secret_manager(project: str, cleanup: CleanupStack) -> None:
    from tools.gcp_secret_manager import (
        check_gcp_secret_manager_requirements,
        list_secrets,
        read_secret,
        write_secret,
    )

    print("\n" + "=" * 60)
    print("Secret Manager")
    print("=" * 60)

    if not check_gcp_secret_manager_requirements():
        raise RuntimeError("Secret Manager requirements failed")

    secret_id = f"phase1-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{os.getpid()}"
    secret_value = f"phase1-value-{datetime.utcnow().isoformat()}"
    cleanup.add(f"secret {secret_id}", lambda: _best_effort_delete_secret(project, secret_id))

    write_result = _json_result(write_secret(secret_id, secret_value, project_id=project))
    _assert_success("secret write", write_result)
    print(f"  wrote: {write_result['secret_id']}")

    read_result = _json_result(read_secret(secret_id, project_id=project))
    _assert_success("secret read", read_result)
    if read_result["value"] != secret_value:
        raise RuntimeError("Secret readback mismatch")

    list_result = _json_result(list_secrets(project_id=project, limit=10))
    _assert_success("secret list", list_result)
    if not any(item["name"] == secret_id for item in list_result.get("secrets", [])):
        raise RuntimeError("Secret not found in list response")


async def smoke_google_chat(project: str, space_id: str) -> None:
    from gateway.platforms.google_chat import GoogleChatAdapter, check_google_chat_requirements
    from gateway.config import PlatformConfig

    print("\n" + "=" * 60)
    print("Google Chat")
    print("=" * 60)

    if not check_google_chat_requirements():
        raise RuntimeError("Google Chat requirements failed")

    adapter = GoogleChatAdapter(PlatformConfig())
    try:
        if not await adapter.connect():
            raise RuntimeError("Google Chat adapter failed to connect")

        send_text = f"[Phase 1 Smoke] {datetime.utcnow().isoformat()}"
        send_result = await adapter.send(space_id, send_text)
        if not send_result.success:
            raise RuntimeError(send_result.error or "send failed")
        print(f"  sent message: {send_result.message_id}")

        info = await adapter.get_chat_info(space_id)
        print(f"  space: {info['name']} ({info['type']})")
    finally:
        await adapter.disconnect()


def smoke_gcp_tasks(project: str, handler_url: str, cleanup: CleanupStack) -> None:
    from tools.gcp_tasks import (
        check_gcp_tasks_requirements,
        create_queue,
        create_task,
        delete_task,
        list_queues,
        list_tasks,
    )

    print("\n" + "=" * 60)
    print("Cloud Tasks")
    print("=" * 60)

    if not check_gcp_tasks_requirements():
        raise RuntimeError("Cloud Tasks requirements failed")

    queue_name = f"phase1-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{os.getpid()}"
    task_name = f"phase1-task-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    cleanup.add(f"queue {queue_name}", lambda: _best_effort_delete_queue(project, queue_name))
    cleanup.add(f"task {task_name}", lambda: _best_effort_json(delete_task, queue_name, task_name, project_id=project))

    queue_result = _json_result(create_queue(queue_name, project_id=project))
    _assert_success("queue create", queue_result)
    print(f"  queue: {queue_result['queue_path']}")

    queues_result = _json_result(list_queues(project_id=project, limit=10))
    _assert_success("queue list", queues_result)

    scheduled_for = datetime.utcnow() + timedelta(minutes=5)
    task_result = _json_result(
        create_task(
            queue_name,
            {
                "run_id": queue_name,
                "handler_url": handler_url,
            },
            task_name=task_name,
            priority="P2",
            schedule_time=scheduled_for,
            project_id=project,
        )
    )
    _assert_success("task create", task_result)
    print(f"  task: {task_result['task_path']}")

    tasks_result = _json_result(list_tasks(queue_name, project_id=project, limit=10))
    _assert_success("task list", tasks_result)


def smoke_gcp_storage(project: str, cleanup: CleanupStack) -> None:
    from tools.gcp_storage import (
        check_gcp_storage_requirements,
        create_bucket,
        delete_blob,
        delete_bucket,
        download_file,
        list_blobs,
        list_buckets,
        upload_file,
    )

    print("\n" + "=" * 60)
    print("Cloud Storage")
    print("=" * 60)

    if not check_gcp_storage_requirements():
        raise RuntimeError("Cloud Storage requirements failed")

    bucket_name = f"{project.lower().replace('_', '-')}-phase1-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{os.getpid()}"
    blob_name = "phase1/smoke.txt"
    cleanup.add(f"bucket {bucket_name}", lambda: _best_effort_json(delete_bucket, bucket_name, force=True, project_id=project))

    bucket_result = _json_result(create_bucket(bucket_name, project_id=project))
    _assert_success("bucket create", bucket_result)
    print(f"  bucket: {bucket_result['bucket_name']}")

    buckets_result = _json_result(list_buckets(project_id=project, prefix=bucket_name, limit=10))
    _assert_success("bucket list", buckets_result)

    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = Path(tmpdir) / "upload.txt"
        download_path = Path(tmpdir) / "downloaded" / "result.txt"
        source_path.write_text("phase1 smoke upload\n", encoding="utf-8")

        upload_result = _json_result(
            upload_file(bucket_name, str(source_path), destination_blob=blob_name, project_id=project)
        )
        _assert_success("upload", upload_result)

        blobs_result = _json_result(list_blobs(bucket_name, project_id=project, limit=10))
        _assert_success("blob list", blobs_result)

        download_result = _json_result(
            download_file(bucket_name, blob_name, str(download_path), project_id=project)
        )
        _assert_success("download", download_result)
        if download_path.read_text(encoding="utf-8") != source_path.read_text(encoding="utf-8"):
            raise RuntimeError("Downloaded file content mismatch")

        delete_blob_result = _json_result(delete_blob(bucket_name, blob_name, project_id=project))
        _assert_success("blob delete", delete_blob_result)


def _best_effort_json(func: Callable[..., str], *args, **kwargs) -> None:
    payload = _json_result(func(*args, **kwargs))
    if payload.get("error"):
        raise RuntimeError(payload["error"])


def _best_effort_delete_secret(project: str, secret_id: str) -> None:
    cmd = [
        "gcloud",
        "secrets",
        "delete",
        secret_id,
        "--project",
        project,
        "--quiet",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _best_effort_delete_queue(project: str, queue_name: str) -> None:
    cmd = [
        "gcloud",
        "tasks",
        "queues",
        "delete",
        queue_name,
        "--project",
        project,
        "--quiet",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    ok, summary = check_prerequisites()
    if not ok:
        return 1

    cleanup = CleanupStack()
    try:
        smoke_secret_manager(summary["project"], cleanup)
        asyncio.run(smoke_google_chat(summary["project"], summary["space_id"]))
        smoke_gcp_tasks(summary["project"], summary["handler_url"], cleanup)
        smoke_gcp_storage(summary["project"], cleanup)
        return 0
    except Exception as exc:
        print(f"\nFAIL: {exc}")
        return 1
    finally:
        cleanup_failures = cleanup.run()
        if cleanup_failures:
            print("\nCleanup failures:")
            for failure in cleanup_failures:
                print(f"  - {failure}")


if __name__ == "__main__":
    raise SystemExit(main())
