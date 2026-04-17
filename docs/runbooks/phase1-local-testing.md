# Phase 1 Local Testing Runbook

This runbook covers the live smoke path for Google Chat and the GCP tools.
It is intentionally separate from the automated pytest suite, which is the
source of truth for contract behavior.

## What This Validates

- Google Chat runtime auth uses a service-account key with `chat.bot`
- Google Chat space provisioning uses DWD user auth with `chat.spaces.create`
- Secret Manager reads, writes, and lists return JSON envelopes
- Cloud Tasks queue/task creation and listing use the current contracts
- Cloud Storage bucket/object operations work against temporary test data

## Prerequisites

Set these environment variables before running the smoke scripts:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CHAT_DWD_SUBJECT`
- `GOOGLE_CHAT_SPACE_ID`
- `GCP_TASKS_HANDLER_URL`

Optional defaults:

- `GCP_TASKS_LOCATION` defaults to `us-east1`
- `GCP_STORAGE_LOCATION` defaults to `us-east1`

## Create the Test Space

Use the delegated user and a service-account key to create the test Chat space:

```bash
python3 scripts/create-chat-test-space.py
```

The script prints `GOOGLE_CHAT_SPACE_ID` and `GOOGLE_CHAT_SPACE_NAME`.

## Run the Smoke Suite

```bash
python3 scripts/test-phase1-local.py
```

The runner:

- Parses every GCP helper result with `json.loads(...)`
- Uses temporary files for the Storage upload/download round trip
- Uses the configured Cloud Tasks handler URL
- Cleans up tasks, queues, buckets, and secrets best-effort on exit

## Cleanup Notes

Cleanup is automatic on the success path. If a cleanup step fails, the runner
prints the resource name and the error so you can remove it manually.

For manual cleanup:

- Delete the Chat test space from Google Chat
- Delete any leftover task queues with `gcloud tasks queues delete`
- Delete any leftover test secrets with `gcloud secrets delete`

