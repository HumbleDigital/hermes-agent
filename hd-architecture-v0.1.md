# HumbleDigital Agent Platform Architecture v0.1

**Status:** Phase 0 Implementation Complete  
**Last Updated:** 2026-04-16  
**Project:** hd-hermes-agent  
**GCP Project:** humble-ops  

---

## Executive Summary

HumbleDigital is a single-user deployment model where one human operates one Workspace seat with multiple AI agents. Agents impersonate the single seat via Domain-Wide Delegation (DWD). Inter-agent communication uses Google Tasks for work queues and Google Chat for visibility/audit trail.

**Key Decisions:**
- 1 human / 1 Workspace seat / multiple agents per deployment
- DWD for all agent auth (agents impersonate single seat)
- Google Tasks for inter-agent work queues (P1/P2/P3 priority, FIFO)
- Google Chat for visibility/audit trail
- Hermes default agent = ops/SRE
- Container-per-client isolation model
- File-based mailbox rejected in favor of Tasks+Chat

---

## Phase Roadmap

### Phase 0: Foundation (COMPLETE ✓)

**Goal:** Establish core GCP integrations and agent platform basics.

**Completed:**
- [x] GCP project `humble-ops` with org-level IAM
- [x] Service accounts: `hermes@`, `dinesh@`, `belvedere@`, `wolff@`
- [x] Domain-Wide Delegation configured for 3 agents
- [x] GCP Secret Manager tool (`tools/gcp_secret_manager.py`)
  - Tools: `gcp_secret_read`, `gcp_secret_write`, `gcp_secret_list`
  - Requires: `google-cloud-secret-manager>=2.23.0`
  - Auth: ADC or `GOOGLE_APPLICATION_CREDENTIALS`
  - **Tested:** ✓ Successfully listed 3 secrets, read secret values
- [x] Google Chat gateway adapter (`gateway/platforms/google_chat.py`)
  - Webhook mode for incoming messages
  - Chat API for outgoing responses
  - DWD auth with scope `https://www.googleapis.com/auth/chat.bot`
  - Thread support (spaces and DMs)
- [x] Google Tasks tool (`tools/gcp_tasks.py`)
  - Tools: `gcp_tasks_create`, `gcp_tasks_list`, `gcp_tasks_delete`, `gcp_tasks_create_queue`, `gcp_tasks_list_queues`
  - Requires: `google-cloud-tasks>=2.17.0`
  - Default location: `us-east1` (NY proximity)
  - Priority levels: P1 (high), P2 (normal), P3 (low)
  - **Tested:** ⚠️ Code working, IAM permission needed (`roles/cloudtasks.admin`)
- [x] Google Cloud Storage tool (`tools/gcp_storage.py`)
  - Tools: `gcp_storage_create_bucket`, `gcp_storage_list_buckets`, `gcp_storage_delete_bucket`, `gcp_storage_upload`, `gcp_storage_download`, `gcp_storage_list_blobs`, `gcp_storage_delete_blob`
  - Requires: `google-cloud-storage>=2.18.0`
  - Default location: `us-east1`
  - Bucket naming: `hd-<client-id>-data`
  - **Tested:** ✓ Successfully listed 4 buckets in humble-ops
- [x] GCP dependencies consolidated in `pyproject.toml` optional `gcp` group:
  ```toml
  gcp = [
      "google-cloud-secret-manager>=2.23.0,<3",
      "google-cloud-tasks>=2.17.0,<3",
      "google-cloud-storage>=2.18.0,<3",
      "google-api-python-client>=2.157.0,<3",
      "google-auth>=2.37.0,<3",
      "google-auth-httplib2>=0.2.0,<1",
  ]
  ```

**Pending:**
- [ ] Grant Cloud Tasks IAM permissions to service account
- [ ] Test Google Chat webhook registration
- [ ] Test Tasks queue creation and dispatch

---

### Phase 1: Single-User Deployment (IN PROGRESS)

**Goal:** Deploy first production agent with single-user model.

**Planned:**
- [ ] Deploy Hermes ops agent to Cloud Run
- [ ] Configure Google Chat webhook for ops channel
- [ ] Create `hermes-ops-queue` in Cloud Tasks
- [ ] Set up Cloud Storage bucket `hd-hermes-ops-data`
- [ ] Configure Secret Manager for API keys
- [ ] Document onboarding flow (Hermes interviews client, provisions agents)

**Infrastructure:**
- Cloud Run for webhooks and agent runtime
- Cloud Tasks for work queues
- Cloud Storage for per-client buckets
- Secret Manager for credentials

**Cost Target:** ~$49/mo (e2-standard-2 + SSD + GCS in us-east1)  
**Runway:** $300 credit = 6+ months

---

### Phase 2: Inter-Agent Communication

**Goal:** Enable multi-agent coordination via Tasks + Chat.

**Planned:**
- [ ] Agent catalog with templates (ops, research, dev, support)
- [ ] Task payload schema standardization
- [ ] Priority routing (P1 → immediate, P2 → FIFO, P3 → batched)
- [ ] Chat audit trail for all agent actions
- [ ] Task completion callbacks

**Architecture:**
```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Agent A    │────▶│  Cloud Tasks │────▶│  Agent B    │
│  (ops)      │     │  (queue)     │     │  (research) │
└─────────────┘     └──────────────┘     └─────────────┘
       │                    │                    │
       │                    ▼                    │
       │            ┌──────────────┐            │
       └───────────▶│  Google Chat │◀───────────┘
                    │  (audit)     │
                    └──────────────┘
```

---

### Phase 3: Ops/Monitoring Agent

**Goal:** Hermes as dedicated ops/SRE agent.

**Planned:**
- [ ] Health monitoring (agent uptime, task queue depth, error rates)
- [ ] Log aggregation and alerting
- [ ] Automated incident response playbooks
- [ ] Cost monitoring and optimization
- [ ] Security scanning (secret rotation, IAM audits)

**Metrics:**
- Agent availability (%)
- Task completion time (p50, p95, p99)
- Queue depth (by priority)
- GCP cost breakdown

---

### Phase 4: Multi-Client Isolation

**Goal:** Container-per-client isolation model.

**Planned:**
- [ ] Client onboarding automation
- [ ] Per-client bucket provisioning (`hd-<client>-data`)
- [ ] Per-client queue provisioning (`hermes-<client>-queue`)
- [ ] IAM isolation (service account per client)
- [ ] Billing allocation per client

**Security:**
- No cross-client data access
- Separate service accounts per client
- Audit logs per client

---

### Phase 5: Scale & Optimization

**Goal:** Production-scale deployment.

**Planned:**
- [ ] Auto-scaling Cloud Run instances
- [ ] Task queue sharding for high throughput
- [ ] Multi-region deployment (us-east1, us-west1, eu-west1)
- [ ] Cost optimization (committed use discounts, spot instances)
- [ ] Performance tuning (latency, throughput)

**Targets:**
- 100+ concurrent agents
- <100ms task dispatch latency
- 99.9% availability

---

## Technical Specifications

### Authentication

**Domain-Wide Delegation (DWD):**
- Service account impersonates Workspace user
- Scope: `https://www.googleapis.com/auth/chat.bot` (Chat API)
- Scope: `https://www.googleapis.com/auth/tasks` (Tasks API)
- Credentials: ADC at `~/.config/gcloud/application_default_credentials.json`

**Environment Variables:**
```bash
GOOGLE_CLOUD_PROJECT=humble-ops
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json  # Optional if ADC
GCP_TASKS_LOCATION=us-east1
GCP_STORAGE_LOCATION=us-east1
GOOGLE_CHAT_WEBHOOK_SECRET=<secret>  # For webhook validation
```

### Toolsets

**GCP Toolsets (registered in `toolsets.py`):**
- `gcp-secret-manager`: Secret read/write/list
- `gcp-tasks`: Task/queue CRUD operations
- `gcp-storage`: Bucket/blob operations

**Gateway Platforms:**
- `google_chat`: Google Chat adapter (webhook + Chat API)
- `telegram`, `discord`, `slack`, etc. (existing)

### Deployment

**Cloud Run:**
- Container: `hd-hermes-agent` (Python 3.11+)
- Memory: 2GB (configurable)
- CPU: 1 vCPU (configurable)
- Timeout: 300s (max for Cloud Run)

**Cloud Tasks:**
- Queue: `hermes-<agent>-queue`
- Location: `us-east1`
- Rate limit: 100 dispatches/sec (configurable)
- Max concurrent: 50 (configurable)

**Cloud Storage:**
- Bucket: `hd-<client>-data`
- Location: `us-east1`
- Storage class: STANDARD (active), NEARLINE (archive)
- Lifecycle rules: 30-day deletion for temp objects

---

## Open Questions

### Phase 1
1. What is the webhook URL for Google Chat integration?
2. Should we use Cloud Run or GCE for agent runtime?
3. How do we handle secret rotation?

### Phase 2
4. What is the task payload schema for inter-agent communication?
5. How do we handle task failures and retries?
6. Should tasks be idempotent?

### Phase 3
7. What metrics should the ops agent monitor?
8. How do we alert on incidents (Chat, email, SMS)?
9. What playbooks should be automated?

### Phase 4
10. How do we onboard new clients?
11. What is the billing model per client?
12. How do we enforce isolation?

### Phase 5
13. What is the target latency for task dispatch?
14. How do we handle multi-region data residency?
15. What is the disaster recovery plan?

---

## Appendix: File Inventory

**Tools:**
- `tools/gcp_secret_manager.py` (333 lines) - Secret Manager integration
- `tools/gcp_tasks.py` (500+ lines) - Cloud Tasks integration
- `tools/gcp_storage.py` (600+ lines) - Cloud Storage integration

**Gateway:**
- `gateway/platforms/google_chat.py` (408 lines) - Google Chat adapter

**Config:**
- `gateway/config.py` - Platform enum (GOOGLE_CHAT added)
- `gateway/run.py` - Adapter factory (GOOGLE_CHAT case added)
- `toolsets.py` - Toolset definitions (gcp-tasks, gcp-storage added)
- `model_tools.py` - Tool discovery (gcp_tasks, gcp_storage added)
- `pyproject.toml` - GCP dependencies (6 packages in `gcp` group)

**Tests:**
- `tests/tools/test_gcp_secret_manager.py` - Secret Manager tests (exists)
- `tests/tools/test_gcp_tasks.py` - Cloud Tasks tests (TODO)
- `tests/tools/test_gcp_storage.py` - Cloud Storage tests (TODO)
- `tests/gateway/test_google_chat.py` - Google Chat adapter tests (TODO)

---

## Changelog

### 2026-04-16
- Created Google Chat gateway adapter
- Implemented Google Tasks tool (5 tools)
- Implemented Cloud Storage tool (7 tools)
- Added GCP dependencies to pyproject.toml
- Updated toolsets.py with gcp-tasks and gcp-storage
- Updated model_tools.py for tool discovery
- Updated gateway/run.py with platform_env_map for Google Chat
- Architecture doc created (this file)

### Prior
- GCP Secret Manager tool implemented
- GCP project `humble-ops` created
- Service accounts created (`hermes@`, `dinesh@`, `belvedere@`, `wolff@`)
- DWD configured for 3 agents
