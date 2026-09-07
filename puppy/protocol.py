"""Wire contract shared by the full console and headless backend nodes.

Controllers and backends must speak the current protocol. Bump API_PROTOCOL
when removing or changing a wire contract incompatibly; additive fields and
capabilities do not require it. Capabilities also describe optional services.
"""
from __future__ import annotations

API_PROTOCOL = 2
SUPPORTED_BACKEND_PROTOCOLS = (API_PROTOCOL,)

# The node owns scratch creation, reset, deletion and missing-workspace recovery.
# The wire name describes the session-owned lifecycle, not OS-temp storage.
TEMPORARY_WORKSPACE_CAPABILITY = "temporary-workspaces"
USAGE_REFRESH_CAPABILITY = "engine-usage-refresh"
MANUAL_USAGE_REFRESH_CAPABILITY = "engine-usage-refresh-manual"
SHARED_USAGE_CAPABILITY = "account-quota-v1"
FILE_UPLOAD_CAPABILITY = "file-uploads"
# Forced installed/latest checks and vendor-delegated engine CLI upgrades.
ENGINE_UPGRADE_CAPABILITY = "engine-upgrade"
# Managed-browser status and enable toggle. Availability (a usable binary) and
# the enable switch are reported dynamically by /api/browser/status.
BROWSER_CAPABILITY = "browser"
# Independent four-character browser instances, create/delete routes, and
# the ID-scoped screencast/input WebSocket.
BROWSER_INSTANCES_CAPABILITY = "browser-instances"
# Per-viewer, correlated CSS cursor hints over the identified browser socket.
BROWSER_CURSOR_CAPABILITY = "browser-cursor"
# Assign one identified browser to a chat, expose its binding to viewers,
# and change it through the authenticated instance API.
BROWSER_HANDOFF_CAPABILITY = "browser-handoff"
# Turn-bound browser file helpers: a chat may place one of its own Puppy upload
# records into a file input, and inspect downloads belonging to its bound
# browser. Neither operation accepts an arbitrary caller-supplied path.
BROWSER_FILE_WORKFLOWS_CAPABILITY = "browser-file-workflows"
# Node-owned shared sign-in store: POST /api/browser/shared-storage and the
# shared_storage field on /api/browser/status.
BROWSER_SHARED_STORAGE_CAPABILITY = "browser-shared-storage"
# GET/PATCH of the node's unattended engine-update schedule. The upgrade itself
# is the engine-upgrade surface; this only decides when the node runs it, so a
# node may support upgrading by hand without advertising the scheduler.
ENGINE_AUTO_UPGRADE_CAPABILITY = "engine-auto-upgrade"
# GET/PUT per-engine starting choices; engines payloads carry session_defaults.
ENGINE_DEFAULTS_CAPABILITY = "engine-defaults"
# GET/PATCH of the node-owned engine cache timers. The same payload also
# carries controller-only synchronization intervals; remote consoles expose
# those only for their own controller, never as if a headless node used them.
TIMER_SETTINGS_CAPABILITY = "timer-settings"
# Node-owned execution/unattended deadlines, including zero = unlimited for
# turn/spawn limits. Spawn calls with omitted limits use the executing node.
TIMEOUT_SETTINGS_CAPABILITY = "timeout-settings"
# GET/PATCH of node-owned custom, remote-workspace, and browser system-prompt
# text. Conditional guidance remains turn-scoped: remote-workspace text needs a
# cross-node mirror, and browser text needs browser tools enabled for the turn.
SYSTEM_PROMPT_CAPABILITY = "system-prompt"
# GET on an upload id returns the stored image, so a preview outlives the
# blob URL held by the page.
UPLOAD_PREVIEW_CAPABILITY = "upload-preview"
# A headless node sends one best-effort ``node_stopping`` event to its update
# and session WebSockets before graceful shutdown waits for/interrupts turns.
# Full WebUI runtimes do not advertise this: only the separately managed
# backend process owns this lifecycle signal.
SHUTDOWN_NOTICE_CAPABILITY = "shutdown-notice"
# /api/ws/updates sends ordered, revisioned state: an attach snapshot, then
# replacements for session, engine, node, browser and terminal topics. Each
# controller owns one upstream subscription per capable online backend;
# HTTP polling remains available when that subscription is disconnected.
NODE_STATE_STREAM_CAPABILITY = "node-state-stream-v1"
# The session WebSocket accepts active-turn controls with correlated
# completion frames. Native request ids remain turn-scoped idempotency keys;
# the authenticated HTTP routes serve request/response callers as well.
SESSION_CONTROL_WS_CAPABILITY = "session-control-ws-v1"
# Session snapshots expose paused queue indexes; the session socket accepts
# set_queue_paused.
QUEUE_PAUSE_CAPABILITY = "queue-pause"
# Move a waiting prompt into the durable shared composer in the same guarded
# operation that removes it from the queue.
QUEUE_EDIT_CAPABILITY = "queue-edit"
# The switch route queues an engine change behind running/queued work instead
# of refusing it, and answers with the additive ``queued`` flag. Queue payloads
# then contain {kind:"engine"} rows beside {kind:"config"} ones.
QUEUED_ENGINE_SWITCH_CAPABILITY = "queued-engine-switch"
# Permission choices use ordered configuration rows beside model/effort.
# Engine switch rows carry the captured target permission default.
QUEUED_PERMISSION_CAPABILITY = "queued-permission-config"
# Hold automatic dequeue during a live queue drag, then atomically accept
# a revision-guarded permutation.
QUEUE_REORDER_CAPABILITY = "queue-reorder"
# Durable, versioned shared composer drafts in snapshots and session-socket
# edits/broadcasts. The browser journal retains unacknowledged edits.
SESSION_DRAFT_CAPABILITY = "session-drafts"
SESSION_DRAFT_PRESENCE_CAPABILITY = "session-draft-presence"
# Authenticated steering requires an expected_turn_id compare token and
# turn-scoped idempotency. Session payloads expose current readiness.
ACTIVE_TURN_STEERING_CAPABILITY = "active-turn-steering"
# Remote-workspace surfaces are two independent additive roles. A provider can
# lease one local project directory to its controller and serve the streamed
# manifest/fetch/apply protocol over it; a mirror host can run linked sessions
# whose engine works in a private synchronized copy behind per-turn sync
# barriers. Nodes never contact each other: every byte is brokered by the
# controller over the channels it already authenticates.
WORKSPACE_PROVIDER_CAPABILITY = "workspace-provider"
WORKSPACE_MIRROR_CAPABILITY = "workspace-mirror"
# A rebuilt mirror exposes a one-use reset marker in its manifest. The
# controller seeds it from the authoritative workspace and acknowledges only
# after a clean pull, preventing an empty mirror from meaning deletions.
WORKSPACE_MIRROR_RESET_CAPABILITY = "workspace-mirror-reset"
# One-shot spawned-agent execution: POST /api/spawn starts a single
# non-interactive engine run in a named directory on this node, and the
# job routes poll/cancel it. The controller relays a session's cross-node
# spawn requests here and never offers them to a node without this marker.
SPAWN_EXEC_CAPABILITY = "spawn-exec"
# Sliding recognized-progress limits and PATCH /api/spawn/{job_id} to replace
# either live deadline during a run.
SPAWN_LIMITS_CAPABILITY = "spawn-progress-limits"
# POST /api/spawn honors a controller-chosen job_id idempotently. Poll/cancel
# wait behind its in-flight start. The controller registers the relay handle
# before transmitting, so a lost response still leaves a trackable job.
SPAWN_CLIENT_IDS_CAPABILITY = "spawn-client-job-ids"
# Relayed starts carry lease_s. Polls, limit updates and POST /api/spawn/renew
# renew it; a job whose controller stops renewing ends as abandoned once its
# lease lapses. Turn-owned local jobs have no ownership lease.
SPAWN_OWNER_LEASE_CAPABILITY = "spawn-owner-lease"
# GET /api/search searches this node's full transcript history through a
# rebuildable FTS index. Consoles query online, search-capable nodes only.
SEARCH_CAPABILITY = "session-search"
# GET /api/sessions/{sid}/events accepts after_seq and returns subsequent
# events oldest first, supporting bounded windows around search hits.
SESSION_EVENT_WINDOW_CAPABILITY = "session-event-window"
# GET /api/sessions/{sid}/events accepts kind=user, filtering before its limit
# for cursor-paged composer recall across the session's entire stored history.
SESSION_PROMPT_HISTORY_CAPABILITY = "session-prompt-history"
# POST /api/sessions/{sid}/tool runs one engine-native maintenance action:
# "compact" summarizes the native context in place (queued behind pending work
# as an additive runnable {kind:"tool"} row) and "undo" drops the last prompt
# and its reply from the native conversation (idle sessions only; files are
# never touched). Each engine lists what it offers in the engines payload's
# additive tool_options; a console shows the composer's tools menu only here.
SESSION_TOOLS_CAPABILITY = "session-tools"
# PATCH /api/sessions/{sid} accepts a semantic Fast boolean, ordered beside
# model/effort/permission changes. Only an engine that advertises support and
# a model whose live catalog supplies a Fast service tier may enable it; the
# opaque native tier is never part of this wire contract.
SESSION_FAST_MODE_CAPABILITY = "session-fast-mode"
# GET/PUT /api/sessions/{sid}/agent-notes read and replace the AGENTS.md and
# CLAUDE.md files in a session's working directory (exactly those two names,
# bounded in size), and every session payload carries the additive
# agent_notes list naming which of them exist. A console shows the sidebar's
# notes button and its editor only for nodes that advertise this.
SESSION_AGENT_NOTES_CAPABILITY = "session-agent-notes"
# Node-owned pinned flags and session order. Drag-and-drop requires the
# complete starting order and pin cohort as compare tokens.
SESSION_PINNING_CAPABILITY = "session-pinning"
# Durable ordinary-slot recency for cross-backend sidebar merging, plus the
# optional expected_recency reorder compare token (aligned with expected_order).
SESSION_ORDER_RECENCY_CAPABILITY = "session-order-recency"
# A node keeps a bounded, durable sequence of authoritative activity-block
# completions and serves GET /api/completions?after=<seq>. Controllers use it
# for remote completion commands without relying on an open browser.
COMPLETION_EVENTS_CAPABILITY = "completion-events"
# POST /api/sessions/{sid}/ask puts a question alongside an active turn
# without changing its conversation. Session payloads expose readiness, and
# the answer is recorded in separate transcript rows. Offered only by engines
# with a native side-question channel.
SIDE_QUESTION_CAPABILITY = "active-turn-side-question"

SESSION_REFERENCES_CAPABILITY = "session-references"

BASE_CAPABILITIES = (
    # Apply may start one conflict-resolution prompt in the task, for re-review.
    "session-task-conflict-resolution",
    "session-tasks",
    # Task creation accepts explicit engine/model/effort/permission choices.
    "session-task-config",
    # Task creation copies the files a prompt's attachment marker lines stage
    # under Main into the task's own upload storage and rewrites their paths.
    "session-task-attachments",
    # Per-session tasks_enabled PATCH; disabling requires no child tasks.
    "session-tasks-toggle",
    # POST .../tasks/{tid}/remove folds a task's condensed conversation into
    # Main as an info row (subtype session_task_archive) before deleting it,
    # applied rows carry their file list, and the per-session tasks_digest
    # PATCH controls whether Main's turns are told about folded tasks.
    "session-task-fold",
    SESSION_REFERENCES_CAPABILITY,
    "session-short-references",
    "session-communication",
    "session-coordination",
    "sessions",
    "session-stream",
    "approvals",
    "message-queue",
    QUEUE_PAUSE_CAPABILITY,
    QUEUE_EDIT_CAPABILITY,
    QUEUE_REORDER_CAPABILITY,
    SESSION_DRAFT_CAPABILITY,
    SESSION_DRAFT_PRESENCE_CAPABILITY,
    ACTIVE_TURN_STEERING_CAPABILITY,
    SIDE_QUESTION_CAPABILITY,
    # model/effort changes made while work is pending hold their place in the
    # message queue ({kind:"config"} items) instead of applying immediately
    "queued-config",
    # engine switches requested while work is pending join the same queue as
    # additive {kind:"engine"} rows and apply in order; config rows also carry
    # the engine that validated them.
    QUEUED_ENGINE_SWITCH_CAPABILITY,
    QUEUED_PERMISSION_CAPABILITY,
    "uploads",
    FILE_UPLOAD_CAPABILITY,
    "filesystem",
    "engine-switch",
    TEMPORARY_WORKSPACE_CAPABILITY,
    USAGE_REFRESH_CAPABILITY,
    MANUAL_USAGE_REFRESH_CAPABILITY,
    SHARED_USAGE_CAPABILITY,
    ENGINE_UPGRADE_CAPABILITY,
    BROWSER_CAPABILITY,
    BROWSER_INSTANCES_CAPABILITY,
    BROWSER_CURSOR_CAPABILITY,
    BROWSER_HANDOFF_CAPABILITY,
    BROWSER_FILE_WORKFLOWS_CAPABILITY,
    BROWSER_SHARED_STORAGE_CAPABILITY,
    UPLOAD_PREVIEW_CAPABILITY,
    ENGINE_AUTO_UPGRADE_CAPABILITY,
    ENGINE_DEFAULTS_CAPABILITY,
    TIMER_SETTINGS_CAPABILITY,
    TIMEOUT_SETTINGS_CAPABILITY,
    SYSTEM_PROMPT_CAPABILITY,
    WORKSPACE_PROVIDER_CAPABILITY,
    WORKSPACE_MIRROR_CAPABILITY,
    WORKSPACE_MIRROR_RESET_CAPABILITY,
    SPAWN_EXEC_CAPABILITY,
    SPAWN_LIMITS_CAPABILITY,
    SPAWN_CLIENT_IDS_CAPABILITY,
    SPAWN_OWNER_LEASE_CAPABILITY,
    SEARCH_CAPABILITY,
    SESSION_EVENT_WINDOW_CAPABILITY,
    SESSION_PROMPT_HISTORY_CAPABILITY,
    SESSION_TOOLS_CAPABILITY,
    SESSION_FAST_MODE_CAPABILITY,
    SESSION_AGENT_NOTES_CAPABILITY,
    SESSION_PINNING_CAPABILITY,
    SESSION_ORDER_RECENCY_CAPABILITY,
    COMPLETION_EVENTS_CAPABILITY,
    NODE_STATE_STREAM_CAPABILITY,
    SESSION_CONTROL_WS_CAPABILITY,
)
TERMINAL_CAPABILITY = "terminal"
# Identified node-owned PTYs, create/list/delete routes, and the ID-scoped
# viewer WebSocket.
TERMINAL_INSTANCES_CAPABILITY = "terminal-instances"
# Explicitly link one identified terminal to one chat. The linked engine turn
# receives the private terminal MCP bridge and first-use activity events.
TERMINAL_HANDOFF_CAPABILITY = "terminal-handoff"
# POST /api/notify/exec runs a controller-supplied completion command. It is
# part of the node's shell surface, so it exists exactly when terminal does.
NOTIFY_EXEC_CAPABILITY = "notify-exec"

# An HTTPS backend advertises this when its pairing data includes a stable
# SHA-256 leaf-certificate pin. The API token remains application-layer auth;
# TLS provides confidentiality and peer identity on the same port.
TLS_PIN_CAPABILITY = "pinned-tls"

# Signed/staged remote upgrades are advertised only while the external
# health-check/rollback launcher is active. The descriptor must include
# readiness; POST remains the final authority on idle state and races.
UPGRADE_CAPABILITY = "remote-upgrade"
UPGRADE_API_PATH = "/api/node/upgrade"


def execution_capabilities(include_terminal: bool = True) -> list:
    caps = list(BASE_CAPABILITIES)
    if include_terminal:
        caps.append(TERMINAL_CAPABILITY)
        caps.append(TERMINAL_INSTANCES_CAPABILITY)
        caps.append(TERMINAL_HANDOFF_CAPABILITY)
        caps.append(NOTIFY_EXEC_CAPABILITY)
    return caps
