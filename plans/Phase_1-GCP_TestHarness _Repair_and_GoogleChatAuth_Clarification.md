# Phase 1 GCP Test Harness Repair and Google Chat Auth Clarification Plan

## Summary

Repair the broken Phase 1 local-testing flow by making automated tests the source of truth and demoting the current scripts to thin, opt-in smoke runners. At the same time, make Google Chat authentication explicit and correct: use DWD user auth for space provisioning and app auth for bot runtime, because `chat.bot` cannot be used with DWD.

### Chosen auth model

- **Provisioning path:** DWD user auth with a service account impersonating `GOOGLE_CHAT_DWD_SUBJECT`, using `spaces.setup` and the documented user-auth scope `https://www.googleapis.com/auth/chat.spaces.create`.
- **Runtime bot path:** app auth for `GoogleChatAdapter` with `https://www.googleapis.com/auth/chat.bot` for webhook/message send/get behavior.
- The code and docs must stop implying that one Chat credential path covers both jobs.

## Swarm Execution

### Stage 0: Lock shared interfaces in the main session

- Define one shared helper module for Google Chat credentials and scope constants, owned by the controller first so downstream workers have a stable contract.
- Define the smoke-runner contract: scripts parse JSON envelopes from GCP tool helpers, never assume native Python objects.
- Register new pytest markers in `pyproject.toml`: `gcp_live` and `google_chat_live`.

### Stage 1: Parallel workers with disjoint ownership

- **Worker A: Chat/Auth**
  - Own `gateway/platforms/google_chat.py` and a new shared helper module such as `gateway/platforms/google_chat_auth.py`.
  - Add explicit credential builders for `app_runtime` and `dwd_user_provisioning`.
  - Fix the `SendResult` construction bug and adapter lifecycle expectations.
  - Add `tests/gateway/test_google_chat.py`.

- **Worker B: Tool contracts**
  - Own `tests/tools/test_gcp_tasks.py`, `tests/tools/test_gcp_storage.py`, and any minimal production fixes in `tools/gcp_tasks.py` / `tools/gcp_storage.py` required to make current contracts testable and consistent.
  - Follow the existing `tests/tools/test_gcp_secret_manager.py` pattern exactly: mocked clients, `json.loads(...)`, envelope assertions, schema assertions.
  - Add narrow toolset-presence assertions to `tests/test_toolsets.py` for `gcp-secret-manager`, `gcp-tasks`, and `gcp-storage`.

### Stage 2: Dependent worker after Stage 1A interface lands

- **Worker C: Smoke runner and docs**
  - Own `scripts/create-chat-test-space.py`, `scripts/test-phase1-local.py`, and `docs/runbooks/phase1-local-testing.md`.
  - Rebuild both scripts around the shared auth helper and current tool contracts.
  - Make cleanup automatic and best-effort, with printed resource names only on cleanup failure.
  - Update website parity docs if this feature is meant to be supported now:
    - `website/docs/reference/toolsets-reference.md`
    - `website/docs/reference/environment-variables.md`

### Stage 3: Review and integration

- **Worker D: Test parity reviewer**
  - Review config/startup parity after the implementation lands.
  - Extend `tests/gateway/test_config.py` and `tests/gateway/test_allowlist_startup_check.py` only if Google Chat env/config is intended to be first-class in the gateway surface.
- **Final reviewer**
  - Verify spec compliance, file ownership discipline, live-test gating, and that no script reintroduces stale assumptions.

## Implementation Changes

### Google Chat auth and adapter

- Add a shared credential helper that exposes two deliberate entry points:
  - `build_google_chat_app_credentials()` for runtime bot calls with `chat.bot`
  - `build_google_chat_dwd_user_credentials(subject)` for provisioning calls with `chat.spaces.create`
- Update `GoogleChatAdapter` to consume only the app-auth path and document that clearly in tests and runtime errors.
- Fix adapter lifecycle and API correctness:
  - instantiate with `PlatformConfig`
  - require `await connect()` before send/info calls
  - keep `disconnect()` async
  - test the real public info method, not the stale script name
  - return valid `SendResult` objects without unsupported kwargs

### Chat-space creation script

- Rewrite `scripts/create-chat-test-space.py` to use DWD user auth, not ADC masquerading as a service-account file.
- Replace `spaces.create` plus stale body fields with `spaces.setup` for a named space that the delegated user actually owns and can see.
- Persist only the real output needed by downstream smoke tests, primarily `GOOGLE_CHAT_SPACE_ID`.
- Remove dead config output such as `dwd_subject` under a `google_chat:` snippet unless the runtime code actually consumes it.

### Phase 1 smoke runner

- Rewrite `scripts/test-phase1-local.py` as a thin live smoke runner that:
  - parses every GCP tool result with `json.loads(...)`
  - uses current function names and signatures
  - uses temp files for storage upload/download
  - uses the configured Cloud Tasks handler URL rather than a per-task target URL
  - uses the real adapter lifecycle for Chat
- Remove hard-coded project-specific assumptions like `ollama-api-key`.
- Create ephemeral test resources with a single run prefix and centralized cleanup.

### Automated tests

- Add `tests/tools/test_gcp_tasks.py` covering:
  - requirements/env resolution
  - queue create/list response envelopes
  - task create/list response envelopes
  - short queue name vs full queue path
  - schedule-time and custom-name branches
  - permission, not-found, and generic API-error mapping
- Add `tests/tools/test_gcp_storage.py` covering:
  - requirements/env resolution
  - bucket create/list/delete
  - upload/download via temp files
  - list/delete blob behavior
  - destination-dir creation and file-not-found behavior
  - permission, not-found, and generic API-error mapping
- Add `tests/gateway/test_google_chat.py` covering:
  - requirements checks
  - app-auth credential construction
  - connect/send/get-chat-info/disconnect lifecycle
  - webhook message normalization and dispatch
  - regression test for the current `SendResult` bug
- Keep `tests/tools/test_gcp_secret_manager.py` as the contract template and extend only if needed for envelope consistency checks.

## Test Plan

- Fast automated pass:
  - `pytest tests/tools/test_gcp_secret_manager.py tests/tools/test_gcp_tasks.py tests/tools/test_gcp_storage.py tests/gateway/test_google_chat.py tests/test_toolsets.py -q`
- Gateway parity pass if Google Chat env/config becomes first-class:
  - `pytest tests/gateway/test_config.py tests/gateway/test_allowlist_startup_check.py -q`
- Opt-in live smoke validation:
  - create a real Chat test space via `scripts/create-chat-test-space.py`
  - run `scripts/test-phase1-local.py`
  - validate secret create/read/list, queue create/task enqueue/list, bucket upload/download/list/delete, and Chat send/info
- Acceptance criteria:
  - no stale imports or method names in scripts
  - no script assumes native Python returns from GCP tool helpers
  - Chat provisioning and runtime use different, documented auth paths
  - live resource cleanup is automatic in the success path

## Assumptions and Defaults

- Google Chat is a supported surface for this project now, so website reference docs should be brought into parity rather than left aspirational.
- The architecture-level desire for DWD is satisfied for provisioning and other user-context Chat actions; runtime bot messaging remains app-auth by design because `chat.bot` is app-only.
- `scripts/test-phase1-local.py` stays in the repo as an optional smoke runner, not as the primary correctness mechanism.
- Live tests are disabled by default and guarded by explicit env vars plus registered pytest markers.
