# HumbleDigital Agent Platform — Architecture v2

**Version:** 2.0 (Unified)  
**Date:** 2026-04-17  
**Authors:** Scooby Carolan, Gilfoyle  
**Status:** Phase 0 Complete, Phase 1 In Progress  
**Repository:** hd-hermes-agent  
**GCP Project:** humble-ops  

---

## Table of Contents

1. [Part A: Platform Architecture](#part-a-platform-architecture)
2. [Part B: Implementation Architecture](#part-b-implementation-architecture)
3. [Part C: Phase Roadmap](#part-c-phase-roadmap)
4. [Part D: Open Questions & Decision Log](#part-d-open-questions--decision-log)

---

# Part A: Platform Architecture

## 1. Vision

HumbleDigital deploys AI agent teams for solo operators. Each customer gets one Google Workspace seat, a fleet of specialized agents operating under DWD (Domain-Wide Delegation), and a single point of contact — the ops agent (Hermes) — who manages the fleet and escalates to the human when necessary.

## 2. Core Principles

| Principle | Description |
|-----------|-------------|
| **1 human, 1 Workspace, multiple agents** | Every agent acts through one shared Workspace identity via DWD |
| **Agents serve the client** | The client decides how they interact (Chat, email, Telegram, SMS). The Workspace is back-office plumbing they never touch |
| **The ops agent is the control plane** | Hermes provisions, monitors, and repairs. Other agents are the data plane |
| **No bespoke app-state service for V1** | Google Tasks for state, Google Chat for visibility, Google Workspace for identity, Cloud Tasks for dispatch. Thin convention layer on standard APIs. (Cloud Run, Secret Manager, Storage are infrastructure, not custom app logic) |
| **Ship single-user first** | Teams are V2. Multi-tenant shared service is V3 |

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT                                   │
│                    (1 human, 1 Workspace)                       │
│                                                                 │
│  ┌──────────┐                                                   │
│  │  Client   │── Telegram                                       │
│  │ interacts │── Google Chat (DM agents in their workspace)     │
│  │  via:     │── Email (agents have @theirworkspace.net)        │
│  │           │── Future: mobile app, web dashboard              │
│  └──────────┘                                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CLIENT WORKSPACE                             │
│              (Google Workspace — 1 seat)                        │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                      │
│  │  Gmail   │  │ Calendar │  │   Drive  │                      │
│  │ (1 inbox,│  │ (shared) │  │ (shared  │                      │
│  │  labels) │  │          │  │  drives) │                      │
│  └──────────┘  └──────────┘  └──────────┘                      │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                      │
│  │  Tasks   │  │   Chat   │  │  Sheets  │                      │
│  │ (agent   │  │ (agentops│  │ (tracking│                      │
│  │  queues) │  │  space)  │  │  if used)│                      │
│  └──────────┘  └──────────┘  └──────────┘                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AGENT FLEET                                  │
│              (1 container per deployment)                       │
│                                                                 │
│  ┌─────────┐  ┌─────────┐  ┌──────────┐  ┌──────────┐         │
│  │ Hermes  │  │ Dinesh  │  │Belvedere │  │  Wolff   │         │
│  │ (ops)   │  │ (code)  │  │ (life)   │  │ (biz)    │         │
│  │         │  │         │  │          │  │          │         │
│  │ Task    │  │ Task    │  │ Task     │  │ Task     │         │
│  │ queue:  │  │ queue:  │  │ queue:   │  │ queue:   │         │
│  │ hermes  │  │ dinesh  │  │belvedere │  │ wolff    │         │
│  │         │  │         │  │          │  │          │         │
│  │ SA:     │  │ SA:     │  │ SA:      │  │ SA:      │         │
│  │ hermes@ │  │dinesh@  │  │belvedere@│  │ wolff@   │         │
│  └─────────┘  └─────────┘  └──────────┘  └──────────┘         │
│         │           │             │             │               │
│         └───────────┴─────────────┴─────────────┘               │
│                              │                                  │
│                    All impersonate the                          │
│                    single Workspace seat                        │
│                    via DWD                                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  INFRASTRUCTURE                                 │
│              (HumbleDigital GCP Project)                        │
│                                                                 │
│  ┌─────────────────────────────────┐                            │
│  │  Cloud Run / GKE                │                            │
│  │  ┌─────────────────────────┐    │                            │
│  │  │  Hermes container 1     │────│── webhook                  │
│  │  ├─────────────────────────┤    │   (Google Chat,            │
│  │  │  Hermes container 2     │────│    Telegram, etc.)         │
│  │  ├─────────────────────────┤    │                            │
│  │  │  ...                    │    │                            │
│  │  └─────────────────────────┘    │                            │
│  │                                 │                            │
│  │  Cloud Storage                  │                            │
│  │  ┌──────────┐┌──────────┐      │                            │
│  │  │client1-  ││client2-  │      │                            │
│  │  │data      ││data      │      │                            │
│  │  └──────────┘└──────────┘      │                            │
│  └─────────────────────────────────┘                            │
│                                                                 │
│  Service Accounts (per client)                                  │
│  ┌──────────┐┌──────────┐┌──────────┐┌──────────┐              │
│  │ hermes@  ││ dinesh@  ││belvedere@││ wolff@   │              │
│  └──────────┘└──────────┘└──────────┘└──────────┘              │
└─────────────────────────────────────────────────────────────────┘
```

## 4. Single-User Deployment Model

**V1 Scope:** Hermes (ops) + Controller (business ops) only. One client channel (Google Chat). Single GCE instance per client.

**Future (Phase 2+):** Add Architect (coding) and Steward (life ops) profiles. Add Telegram/SMS channels.

```
┌──────────────────────────────────────────────────────┐
│              SINGLE-USER DEPLOYMENT (V1)              │
│                                                       │
│  Google Workspace (1 seat: client@theirworkspace.net) │
│  ┌────────────────────────────────────────────────┐  │
│  │ Gmail    │ Calendar │ Drive │ Tasks │ Chat      │  │
│  │ (1 inbox)│ (shared) │(shared)│(queues)│(agentops)│  │
│  └────────────────────────────────────────────────┘  │
│                                                       │
│  GCP Project (humble-ops — shared for V1)             │
│  ┌────────────────────────────────────────────────┐  │
│  │  GCE VM: hermes-<client> (always-on worker)    │  │
│  │  ┌──────────────────────────────────────┐     │  │
│  │  │ Agent profiles:                       │     │  │
│  │  │  ├── hermes (ops agent)              │     │  │
│  │  │  └── controller (business ops)       │     │  │
│  │  │                                       │     │  │
│  │  │ Storage: Local SSD (SQLite + state)   │     │  │
│  │  │ Cloud Storage: blobs/artifacts only   │     │  │
│  │  │ Cron: in-process scheduler            │     │  │
│  │  └──────────────────────────────────────┘     │  │
│  │                                                │  │
│  │  Cloud Run: webhook ingress only              │  │
│  │  (stateless, autoscaled, Chat/Telegram HTTP)  │  │
│  │                                                │  │
│  │  Service Accounts:                             │  │
│  │  ├── hermes@client-ops.iam.gserviceaccount.com │  │
│  │  └── controller@client-ops.iam.gserviceaccount.com│  │
│  │  (All DWD-impersonate: client@theirworkspace.net)│  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

### Identity Semantics

**Bot Persona vs Workspace Identity:**

- **Bot Persona (Chat identity):** Each agent appears as a distinct persona in Google Chat (Hermes, Architect, Steward, Controller). Messages show agent name/avatar.
- **Workspace Identity (Gmail/Calendar/Drive):** All agents act through the single Workspace seat via DWD. Emails sent from `client@theirworkspace.net` with `From:` header showing agent name. Calendar events on client's primary calendar. Drive files in agent-specific shared drives.

**Why separate?** The human client interacts with distinct agent personalities in Chat, but backend Workspace actions (email, calendar, Drive) all run under delegated user access. This is the correct mental model.

```
  All agents impersonate the single Workspace seat via DWD:

  hermes@client-ops.iam ──DWD──► client@theirworkspace.net
  architect@client-ops.iam ──DWD──► client@theirworkspace.net
  steward@client-ops.iam ──DWD──► client@theirworkspace.net
  controller@client-ops.iam ──DWD──► client@theirworkspace.net

Emails sent: client@theirworkspace.net (with From: header for agent identity)
Calendar events: client's primary calendar
Drive files: stored in agent-specific shared drives
Tasks: per-agent task lists (hermes-queue, dinesh-queue, etc.)
Chat: agentops space (all agents + client)
```

### Unit Economics (Per Client)

| Item | Cost/Month |
|------|-----------|
| Google Workspace (1 seat) | ~$8-12 |
| Cloud Run (1 instance) | ~$10-20 |
| Cloud Storage (data volume) | ~$1-5 |
| GCP Service Accounts | Free |
| **Total** | **~$20-40** |

---

# Part B: Implementation Architecture

## 5. Inter-Agent Communication

### Architecture: Cloud Tasks + Google Tasks + Chat

Agents coordinate work through observable, trackable channels without blocking each other or the human.

**Two-Queue Model:**

| Queue Type | Purpose | Visibility | Implementation |
|------------|---------|------------|----------------|
| **Cloud Tasks** | Internal dispatch — webhook → agent process | Invisible (GCP internal) | `tools/gcp_tasks.py` |
| **Google Tasks** | Agent work queues — human-visible task lists | Visible to client in Workspace | `tools/gcp_tasks.py` (Workspace API) |
| **Google Chat** | Audit trail — all agent broadcasts | Visible to client | `gateway/platforms/google_chat.py` |

**Why both?** Cloud Tasks is for async dispatch (serverless queuing with retry, backoff, rate limiting). Google Tasks is for work tracking (human-readable queues, auditable lifecycle, client visibility). They serve different purposes.

```
┌─────────────────────────────────────────────────────────────┐
│                 INTER-AGENT COMM FLOW                        │
│                                                              │
│  Gilfoyle needs Dinesh to fix a bug:                         │
│                                                              │
│  1. Gilfoyle creates task:                                   │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Task List: dinesh-queue                           │    │
│     │ Title: [P2] Fix auth race condition in PR #142    │    │
│     │ Notes: See PR #142, failing on concurrent token   │    │
│     │        refresh. Repro: curl -X POST /auth/refresh │    │
│     │        with 100 concurrent requests.              │    │
│     │ Status: needsAction                               │    │
│     │ Due: 2026-04-17                                   │    │
│     └──────────────────────────────────────────────────┘    │
│                                                              │
│  2. Gilfoyle broadcasts to agentops Chat space:              │
│     "📋 Created [P2] Fix auth race condition → dinesh"      │
│                                                              │
│  3. Dinesh's cron picks up the task (next heartbeat):        │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Cron: poll dinesh-queue for needsAction tasks     │    │
│     │ → Found [P2] Fix auth race condition              │    │
│     │ → Create session with dinesh profile              │    │
│     │ → Inject task as prompt context                   │    │
│     │ → Agent executes work                             │    │
│     └──────────────────────────────────────────────────┘    │
│                                                              │
│  4. Dinesh posts to agentops Chat:                           │
│     "🔧 Taking [P2] Fix auth race condition"                │
│                                                              │
│  5. Dinesh completes the task:                               │
│     ┌──────────────────────────────────────────────────┐    │
│     │ Task: [P2] Fix auth race condition                │    │
│     │ Status: completed                                 │    │
│     │ Notes: + handoff → [P1] Review PR #148 → gilfoyle │    │
│     └──────────────────────────────────────────────────┘    │
│                                                              │
│  6. Dinesh broadcasts to agentops Chat:                      │
│     "✅ Completed [P2] Fix auth race condition"             │
│     "📤 Handed off [P1] Review PR #148 → gilfoyle"         │
│                                                              │
│  7. Gilfoyle's cron picks up the handoff task.               │
│                                                              │
│  The human (client) sees ALL of this in the agentops space.  │
└─────────────────────────────────────────────────────────────┘
```

### Priority System

| Priority | Meaning | SLA (Target) | Example |
|----------|---------|-------------|---------|
| P1 | Critical / Blocking | Next heartbeat cycle | Production down, auth broken |
| P2 | Standard | Within 24h | Bug fix, feature request |
| P3 | Nice to have | Best effort | Cleanup, docs, refactoring |

- No priority tag = P3 (agents don't self-escalate)
- FIFO within same priority
- P1 tasks must be acknowledged by the receiving agent within 2 heartbeat cycles

### Task Lifecycle

```
needsAction ──► inProgress ──► completed
     │              │               │
     │              │               └──► May create handoff task 
     │              │                    in another agent's queue
     │              │
     │              └──► Agent posts "Taking [P2] ..." to Chat
     │
     └──► Created by any agent or by the human client
```

### Failure Handling

```
needsAction ──► inProgress ──► FAILED
                                   │
                                   └──► Hermes detects (no completion
                                        after N heartbeat cycles)
                                        │
                                        ├──► Hermes retries
                                        │    (re-queues task, increments
                                        │     attempt count in notes)
                                        │
                                        ├──► Hermes escalates to human
                                        │    (creates P1 task in
                                        │     client-queue, posts to Chat)
                                        │
                                        └──► Hermes auto-remediates
                                             (restarts cron, re-provisions
                                              credentials, etc.)
```

### Core Tools (Baked Into Hermes)

| Tool | Purpose | API |
|------|---------|-----|
| `agent_task` | Create, list, complete, handoff tasks | Google Tasks API |
| `agent_broadcast` | Post to agentops Chat space | Google Chat API |
| `agent_heartbeat` | Register alive signal, check other agents | Google Tasks (heartbeat task list) |

These are **not skills**. They are core tools at the same level as `todo` and `memory`. Every profile gets them automatically.

### Profile Config Addition

```yaml
agent_comms:
  task_list: "dinesh-queue"           # Google Tasks list name (inbox)
  chat_space: "spaces/AGENTOPS_ID"    # Google Chat space ID
  cron_interval: "5m"                 # Task pickup frequency
  heartbeat_list: "agent-heartbeats"  # Shared heartbeat task list
```

### Why Not File Mailboxes / PubSub / Custom DB

| Option | Verdict | Reason |
|--------|---------|--------|
| File-based mailbox | Rejected | No tracking, no visibility, no audit trail |
| Custom database | Rejected | Another infra component to manage, backup, scale |
| Google Pub/Sub | Rejected | Wrong abstraction — event streaming, not work queues |
| Google Tasks + Chat | **Selected** | Free, already paid for, observable, queryable, API-native |

## 6. Ops Agent (Hermes Default)

The default Hermes instance is the SRE. It provisions, monitors, and repairs the agent fleet. It escalates to the human only when it can't self-heal.

### Responsibilities

| Function | Description |
|----------|-------------|
| **PROVISIONING** | Create agent profiles from templates, set up DWD service accounts, create task lists/Chat spaces/Drive folders, configure cron schedules |
| **MONITORING** | Check agent heartbeats (task-based keepalive), detect missed cron pickups, watch for FAILED tasks, monitor DWD credential health |
| **REMEDIATION** | Restart failed cron jobs, re-provision expired DWD credentials, re-queue failed tasks, rotate service account keys (if needed) |
| **ESCALATION** | Create P1 task in client-queue, post to agentops Chat with @client, only after self-remediation fails |
| **INTERVIEW** | Client describes their problems/needs, Hermes recommends agent profiles from catalog, client approves or adjusts, Hermes provisions everything |

### Escalation Decision Tree

```
Agent offline / task stuck
    │
    ├── Can Hermes fix it?
    │   ├── YES: Fix it, log to agentops, no human notification
    │   └── NO: Escalate
    │       │
    │       ├── Is it systemic? (multiple agents affected)
    │       │   └── YES: P1 to client + P1 to HumbleDigital
    │       │       (infrastructure-level problem)
    │       │
    │       └── Is it isolated? (one agent affected)
    │           └── P1 to client, attempt workaround
    │               (agent-specific config/cred issue)
    │
    └── Does it require human authority?
        ├── Permission decisions (data access scope)
        ├── Business logic questions (CRM mapping)
        └── Billing / account changes
            └── P1 to client with context
```

### Agent Catalog (Templates)

| Agent Type | Template Includes |
|------------|-------------------|
| **Hermes** (ops) | Monitoring skills, provisioning skills, agent_comms tools, escalation prompts |
| **Architect** (coding) | GitHub CLI, code review, debugging, agent_comms tools, coding system prompt |
| **Steward** (life ops) | Calendar, Gmail, household skills, agent_comms tools, domestic ops prompt |
| **Controller** (business ops) | Sheets, financial tracking, reporting skills, agent_comms tools, business prompt |

**Note:** Agent profile names are configurable. "Architect," "Steward," and "Controller" are functional descriptors — not trademarked character names. Final naming TBD.

Each template: profile config, skill list, DWD scopes, cron schedule, system prompt, task list name, Chat space membership.

### Health Check Convention

Each agent's cron heartbeat writes a task to the shared `agent-heartbeats` list:

```
Title: [heartbeat] <agent-name>
Notes: last_cron=2026-04-15T10:05:00Z status=ok tasks_processed=2
Status: completed (Hermes marks old ones complete)
Due: <next expected heartbeat time>
```

If Hermes doesn't see a heartbeat within 2x the expected interval, it triggers remediation.

### Technical Specifications

### Authentication

**Domain-Wide Delegation (DWD):**
- Service account impersonates Workspace user
- Scope: `https://www.googleapis.com/auth/chat.bot` (Chat API)
- Scope: `https://www.googleapis.com/auth/tasks` (Tasks API)
- Credentials: ADC at `~/.config/gcloud/application_default_credentials.json`

**Target State:** No JSON service account keys. All auth via ADC + IAM signBlob. DWD for Workspace API calls.

**Current Implementation Gap:** Some tools still check for local credential files. DWD subject delegation not yet implemented in Chat adapter. This is Phase 1 work.

**Environment Variables:**
```bash
GOOGLE_CLOUD_PROJECT=humble-ops
GOOGLE_APPLICATION_CREDENTIALS=/path/...json  # Optional if ADC
GCP_TASKS_LOCATION=us-east1
GCP_STORAGE_LOCATION=us-east1
GOOGLE_CHAT_WEBHOOK_SECRET=***  # For webhook validation
```

### Toolsets (Phase 0 Complete)

| Toolset | Tools | Status |
|---------|-------|--------|
| `gcp-secret-manager` | `gcp_secret_read`, `gcp_secret_write`, `gcp_secret_list` | ✓ Complete, tested |
| `gcp-tasks` | `gcp_tasks_create`, `gcp_tasks_list`, `gcp_tasks_delete`, `gcp_tasks_create_queue`, `gcp_tasks_list_queues` | ✓ Code complete, IAM pending |
| `gcp-storage` | `gcp_storage_create_bucket`, `gcp_storage_list_buckets`, `gcp_storage_delete_bucket`, `gcp_storage_upload`, `gcp_storage_download`, `gcp_storage_list_blobs`, `gcp_storage_delete_blob` | ✓ Complete, tested |
| `google_chat` | Gateway platform adapter (webhook + Chat API) | ✓ Complete |

### File Inventory (Phase 0)

| File | Lines | Description |
|------|-------|-------------|
| `tools/gcp_secret_manager.py` | 333 | Secret Manager integration |
| `tools/gcp_tasks.py` | 500+ | Cloud Tasks integration |
| `tools/gcp_storage.py` | 600+ | Cloud Storage integration |
| `gateway/platforms/google_chat.py` | 408 | Google Chat adapter |

### Deployment

**Runtime Model: Cloud Run (ingress) + GCE (worker)**

- **Cloud Run**: Webhook ingress only (Google Chat, Telegram, etc.). Stateless, autoscaled, handles HTTP callbacks.
- **GCE VM**: Always-on Hermes worker process. Runs the in-process cron scheduler, SQLite databases, background task polling. Single-instance per client.

**Why this split?** Cloud Run is stateless and autoscales — wrong fit for in-process cron + local SQLite. GCE provides persistent disk, always-on execution, and local file locking for the scheduler. This is the V1 model.

**Future (Phase 5):** If scale-out is required, migrate scheduler to Cloud Tasks triggers + external database (Firestore or Cloud SQL).

**Cloud Run:**
- Container: `hd-hermes-agent` (Python 3.11+)
- Memory: 2GB (configurable)
- CPU: 1 vCPU (configurable)
- Timeout: 300s (max for Cloud Run)
- Purpose: Webhook endpoints only

**GCE VM:**
- Machine type: e2-standard-2 (2 vCPU, 8GB RAM)
- Boot disk: 50GB SSD (SQLite + logs + local state)
- Zone: us-east1-b (existing NAT router)
- Purpose: Always-on worker, cron scheduler, SQLite databases

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
- **Purpose: Object storage only (blobs, artifacts, backups). NOT SQLite backing.**

### SQLite Strategy

- **V1 (single-instance):** SQLite on local GCE boot disk. Fast, simple, no network latency.
- **Constraint:** Single-instance only. No concurrent writes from multiple processes.
- **Future (Phase 5):** Migrate to Cloud SQL (PostgreSQL) or Firestore if scale-out required.
- **Never:** Do NOT use Cloud Storage (GCS) as SQLite backing — GCS is object storage, not a filesystem.

### GCP Dependencies (pyproject.toml)

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

---

# Part C: Phase Roadmap

## Phase 0: Foundation ✓ COMPLETE

**Goal:** Establish core GCP integrations and agent platform basics.

**Completed:**
- [x] GCP project `humble-ops` with org-level IAM
- [x] Service accounts: `hermes@`, `dinesh@`, `belvedere@`, `wolff@`
- [x] Domain-Wide Delegation configured for 3 agents
- [x] GCP Secret Manager tool (`tools/gcp_secret_manager.py`)
- [x] Google Chat gateway adapter (`gateway/platforms/google_chat.py`)
- [x] Google Tasks tool (`tools/gcp_tasks.py`)
- [x] Google Cloud Storage tool (`tools/gcp_storage.py`)
- [x] GCP dependencies consolidated in `pyproject.toml` optional `gcp` group

**Pending:**
- [ ] Grant Cloud Tasks IAM permissions to service account
- [ ] Test Google Chat webhook registration
- [ ] Test Tasks queue creation and dispatch

## Phase 1: Single-User Deployment (IN PROGRESS)

**Goal:** Deploy first production agent (Hermes + Controller) with single-user model.

**Planned:**
- [ ] Deploy Hermes ops agent to GCE VM (always-on worker)
- [ ] Configure Google Chat webhook for ops channel (Cloud Run ingress)
- [ ] Create `hermes-ops-queue` in Google Tasks (Workspace product)
- [ ] Set up Cloud Storage bucket `hd-hermes-ops-data` (blobs only)
- [ ] Configure Secret Manager for API keys
- [ ] Implement DWD subject delegation in Chat adapter
- [ ] Document onboarding flow (Hermes interviews client, provisions agents)

**Infrastructure:**
- GCE VM for always-on worker (cron scheduler, SQLite, task polling)
- Cloud Run for webhooks (stateless ingress)
- Google Tasks for work queues (visible to client)
- Cloud Tasks for internal dispatch (invisible)
- Cloud Storage for blobs/artifacts
- Secret Manager for credentials

**Cost Target:** ~$49/mo (e2-standard-2 + SSD + GCS in us-east1)  
**Runway:** $300 credit = 6+ months

**V1 Scope Constraint:** Hermes + Controller only. Google Chat only. Single client. No Workspace provisioning automation (manual setup for first deployment).

## Phase 2: Inter-Agent Communication

**Goal:** Enable multi-agent coordination via Tasks + Chat.

**Planned:**
- [ ] Agent catalog with templates (ops, research, dev, support)
- [ ] Task payload schema standardization
- [ ] Priority routing (P1 → immediate, P2 → FIFO, P3 → batched)
- [ ] Chat audit trail for all agent actions
- [ ] Task completion callbacks

## Phase 3: Ops/Monitoring Agent

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

## Phase 4: Client Onboarding & Provisioning

**Goal:** From payment to agents working in under 10 minutes. Zero manual steps by HumbleDigital staff.

**V1 Reality Check:** Phase 4 is too wide for V1. First deployment will have manual Workspace setup, manual DWD configuration, and HumbleDigital staff involvement. The zero-touch goal is the target state, not the V1 reality.

**Two Onboarding Tracks (Future):**
1. **Managed HumbleDigital Workspace** — We create the Workspace, domain, DWD, everything. Client pays one bill.
2. **Bring Your Own Workspace** — Client has existing Workspace, we configure DWD and deploy agents. Faster, less control.

For V1: Track 1 only (managed). Track 2 is Phase 3 work.

**Onboarding Flow:**
```
Client Pays
    │
    ▼
HumbleDigital provisioning (automated by HumbleDigital's Hermes)
    ├── Create GCP project for client (or use shared project)
    ├── Provision Workspace:
    │   ├── Register domain (or use client's existing)
    │   ├── Create Google Workspace (1 seat)
    │   ├── Verify domain
    │   └── Enable Workspace APIs
    ├── Provision infrastructure:
    │   ├── Create Cloud Storage bucket
    │   ├── Deploy Hermes container
    │   ├── Configure webhook routing
    │   └── Set up TLS cert
    └── Provision identities:
        ├── Create GCP project (client-ops)
        ├── Create service accounts (hermes, dinesh, belvedere, wolff)
        ├── Authorize DWD in Workspace admin console
        └── Configure OAuth consent screens
    │
    ▼
Client's Hermes instance boots (first run)
    │
    ├── Hermes (ops agent) initializes:
    │   ├── Creates task lists (hermes-queue, dinesh-queue, etc.)
    │   ├── Creates agentops Chat space
    │   ├── Adds all agents to Chat space
    │   └── Posts "Hello, I'm Hermes" to Chat
    │
    ├── Hermes interviews the client:
    │   ├── "What do you do? What are your biggest pain points?"
    │   ├── "Do you need help with code, life management, or business operations?"
    │   └── Recommends agent profiles based on answers
    │
    ├── Client approves recommendations
    │
    └── Hermes provisions agents:
        ├── Creates agent profiles from templates
        ├── Configures DWD per agent
        ├── Sets up cron schedules
        ├── Creates agent-specific Drive folders
        └── Each agent posts introduction to agentops Chat
    │
    ▼
AGENTS LIVE. Client starts working.
```

**What the Client Sees (First 10 Minutes):**
1. **Payment confirmation** — "Welcome to HumbleDigital. Your workspace is being set up."
2. **Chat invitation** — "Your Hermes agent is ready. Say hello: [link to Google Chat DM]"
3. **Interview** — Hermes asks what the client needs
4. **Agent introductions** — Recommended agents appear in the Chat space with name, role, capabilities
5. **Working** — Client starts assigning tasks naturally through conversation

The client never touches Admin Console. Never creates a service account. Never configures DWD scopes. Hermes does all of it.

## Phase 5: Multi-Tenancy & Scale

**Goal:** Support 50+ client deployments efficiently. This is the V3 concern — don't build it until you need it.

### Cost Projection (50 Clients)

| Item | Per Client/Mo | 50 Clients/Mo |
|------|-------------|---------------|
| Google Workspace (1 seat) | $8 | $400 |
| Cloud Run (1 instance) | $15 | $750 |
| Cloud Storage | $3 | $150 |
| Domain cost (amortized) | $1 | $50 |
| **Total** | **~$27** | **~$1,350** |

At $99/mo pricing (hypothetical), that's ~$3,600/mo revenue vs ~$1,350/mo cost = 63% margin.

**Note:** These are rough estimates. Cloud Run pricing varies with traffic. Actual margins depend on usage patterns.

### Per-Client Isolation

- **Data**: Separate Cloud Storage bucket, separate SQLite database
- **Identity**: Separate Workspace tenant (or subdomain)
- **Compute**: Separate container (Cloud Run revision or GKE pod)
- **Network**: Separate TLS cert, separate webhook URL
- **Auth**: Separate service accounts, separate DWD delegation

No multi-tenant shared database. No shared process. Each client's data only lives in their container.

---

# Part D: Open Questions & Decision Log

## Security

| Principle | Implementation |
|-----------|----------------|
| **No JSON service account keys** | All auth via ADC + IAM signBlob. Org policies enforce this |
| **DWD scopes are exact-match** | Authorized scope strings must match requested scopes character-for-character |
| **No shared credentials between clients** | Each client has its own GCP project, its own service accounts, its own Workspace |
| **Webhook verification** | Google Chat webhooks must verify incoming requests are from Google (token validation) |
| **Data isolation** | Per-client containers, per-client storage, no cross-tenant data access |

## Observability

- **Per-agent heartbeats** — Each agent posts alive signals to a shared task list on its cron schedule
- **Hermes monitors heartbeats** — Missing 2+ heartbeats = trigger remediation
- **Agentops Chat space** — All agent broadcasts are visible to the client and to Hermes. Full audit trail
- **Task state is the source of truth** — Any task can be queried via Tasks API. needsAction = waiting, completed = done

## Reliability

- **Cron is the pickup mechanism** — If a cron job dies, it's a heartbeat gap → Hermes detects → Hermes restarts
- **Tasks API retries** — Google Tasks is eventually consistent. Agent code must handle 409 conflicts (etag-based)
- **Chat webhooks retry** — Google Chat retries failed HTTP calls within a few minutes. Agent endpoints must be idempotent

## Google Workspace Limitations

| Limitation | Workaround |
|------------|------------|
| **Task notes: 4,096 character limit** | Large context (code, diffs, logs) must be referenced by URL (PR link, file path) rather than inline |
| **No assignee field in Tasks API** | Agent identity is encoded in task list name (`architect-queue`, `steward-queue`) |
| **No webhook on task completion** | Crons must poll. Acceptable for agent cadence (5m intervals) |
| **Google Chat requires @mention for bot in spaces** | DMs work without mention. Space messages must @mention the bot |
| **Workspace seat cost** | 1 seat per client, not per agent. Agents share the seat via DWD |

## Naming Conventions

| Entity | Convention | Example |
|--------|-----------|---------|
| Service account | `{agent}@{client}-ops.iam.gserviceaccount.com` | `architect@acme-ops.iam.gserviceaccount.com` |
| Task list | `{agent}-queue` | `architect-queue`, `hermes-queue` |
| Chat space | `agentops-{client}` | `agentops-acme` |
| Drive folder | `{agent} (shared drive)` | `Architect (shared drive)` |
| Container | `hermes-{client}` | `hermes-acme` |
| Storage bucket | `hermes-{client}-data` | `hermes-acme-data` |

## Open Questions

### Must Decide Before Phase 2

1. **Agent catalog depth** — How many agent templates ship with V1? Start with Hermes + Dinesh? Or include Belvedere and Wolff?
2. **Task note size workaround** — Do we compress context into task notes, or always reference external resources (GitHub, Drive links)?
3. **Heartbeat granularity** — 5 minutes? 1 minute? Trade-off between API quota and detection latency.
4. **Chat space naming** — Are agentops spaces per-client or global? Per-client seems right for isolation.

### Must Decide Before Phase 3

5. **Hermes interview format** — How structured? Free-form conversation → agent recommendation? Or menu selection?
6. **Remediation boundary** — What can Hermes auto-fix vs. what must always escalate? Document the specific actions.
7. **Self-healing scope** — Can Hermes modify agent profiles? Restart containers? Re-provision service accounts? Where's the line?

### Must Decide Before Phase 4

8. **Domain strategy** — Does each client get their own domain, or a subdomain of humbledigital.net? (e.g., `acme.humbledigital.net` vs `acme.com`)
9. **Workspace provisioning automation** — Can the Admin SDK fully automate Workspace creation, or is there manual Console work?
10. **Client channel preference** — Do we force Google Chat as the primary interface, or support Telegram/Email as the main channel from day one?
11. **Billing model** — Per-seat (1 seat = 1 deployment)? Tiered (starter = 2 agents, pro = 4 agents)?

### Must Decide Before Phase 5

12. **Shared vs. dedicated GCP project** — Each client gets their own project, or share a project with IAM isolation?
13. **Container orchestration** — Cloud Run (serverless, per-revision), GKE (shared cluster, pods), or something else?
14. **Monitoring stack** — Do we build custom monitoring on top of Hermes heartbeats, or use Cloud Monitoring / Prometheus?
15. **Update rollouts** — How do we push new Hermes versions to 50 containers simultaneously? Rolling update? Canary?

---

## Changelog

### v2.0 (2026-04-17)
- Unified three v0.1 documents into single coherent architecture
- Restructured into 4 parts: Platform, Implementation, Roadmap, Open Questions
- Added detailed Phase 0 completion status with file inventory
- Consolidated all ASCII diagrams and tables
- Preserved all open questions from v0.1 docs

### v0.1 (2026-04-15/16)
- Original architecture documents created
- Phase 0 implementation complete (GCP tools, Google Chat adapter)
- Phase roadmap defined (Phases 0-5)

---

*End of architecture design document v2.0*
