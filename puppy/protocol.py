"""Wire contract shared by the full console and headless backend nodes.

The application version may move independently on controllers and backends.
Only bump API_PROTOCOL when an incompatible change makes an older peer unsafe
or impossible to support; additive fields and capabilities do not require it.
"""
from __future__ import annotations

API_PROTOCOL = 1
LEGACY_PROTOCOL = 0
SUPPORTED_BACKEND_PROTOCOLS = (LEGACY_PROTOCOL, API_PROTOCOL)

# The node owns create/reset/delete/reboot-expiry semantics for scratch cwd
# paths; old protocol-1 nodes simply omit this additive capability.
TEMPORARY_WORKSPACE_CAPABILITY = "temporary-workspaces"
USAGE_REFRESH_CAPABILITY = "engine-usage-refresh"
MANUAL_USAGE_REFRESH_CAPABILITY = "engine-usage-refresh-manual"
FILE_UPLOAD_CAPABILITY = "file-uploads"
# Covers both engine-version endpoints: the forced installed/latest re-check
# and the vendor-delegated engine CLI upgrade. They ship together, so one
# additive capability keeps the controller from offering either to old nodes.
ENGINE_UPGRADE_CAPABILITY = "engine-upgrade"
# Base managed headless-browser surface: status, the node-owned enable toggle,
# and the legacy screencast/input websocket. Advertising the routes is distinct
# from availability (a usable binary) and from the node's enable switch - both
# are reported dynamically by /api/browser/status.
BROWSER_CAPABILITY = "browser"
# Independent four-character browser instances, their create/delete routes,
# and the ID-scoped screencast websocket. Older browser-capable nodes expose
# only the legacy singleton websocket and remain usable through that fallback.
BROWSER_INSTANCES_CAPABILITY = "browser-instances"
# Explicitly assigning one named browser to one chat, reporting that binding to
# viewers, and changing it through the authenticated instance API. Older nodes
# still accept an ID when an agent names it, but cannot make that relationship
# visible or user-controlled in the WebUI.
BROWSER_HANDOFF_CAPABILITY = "browser-handoff"
# Turn-bound browser file helpers: a chat may place one of its own Puppy upload
# records into a file input, and inspect downloads belonging to its bound
# browser. Neither operation accepts an arbitrary caller-supplied path.
BROWSER_FILE_WORKFLOWS_CAPABILITY = "browser-file-workflows"
# The node-owned shared sign-in store: POST /api/browser/shared-storage and the
# additive shared_storage field on /api/browser/status. Older nodes keep their
# per-browser throwaway profiles and the controller withholds the toggle.
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
# GET/PATCH of node-owned custom, remote-workspace, and browser system-prompt
# text. Conditional guidance remains turn-scoped: remote-workspace text needs a
# cross-node mirror, and browser text needs browser tools enabled for the turn.
SYSTEM_PROMPT_CAPABILITY = "system-prompt"
# GET on an upload id returns the stored image, so a preview outlives the blob
# URL a page held. Older nodes store the same files but expose no way to read
# them back; the controller falls back to the named-file chip there.
UPLOAD_PREVIEW_CAPABILITY = "upload-preview"
# A headless node sends one best-effort ``node_stopping`` event to its update
# and session WebSockets before graceful shutdown waits for/interrupts turns.
# Full WebUI runtimes do not advertise this: only the separately managed
# backend process owns this lifecycle signal.
SHUTDOWN_NOTICE_CAPABILITY = "shutdown-notice"
# `/api/ws/updates` is an ordered, revisioned node-state stream.  It sends a
# full snapshot on every attach and then replaces session, engine, node,
# browser and terminal topics as their owners change.  A controller may keep
# one upstream subscription for a capable backend and stop browser-driven
# polling; older nodes remain on the existing HTTP timers.
NODE_STATE_STREAM_CAPABILITY = "node-state-stream-v1"
# The existing per-session WebSocket accepts the two active-turn controls and
# returns a correlated completion frame for each handoff.  The native request
# id remains the turn-scoped idempotency key; this marker only changes how the
# browser transports it.  Older nodes retain their authenticated HTTP routes.
SESSION_CONTROL_WS_CAPABILITY = "session-control-ws-v1"
# Session snapshots/broadcasts expose paused queue indexes and the session
# socket accepts ``set_queue_paused``. Older controllers ignore the field;
# newer controllers hide the control until a remote node advertises support.
QUEUE_PAUSE_CAPABILITY = "queue-pause"
# The session socket can move one still-waiting prompt into the durable shared
# composer in the same guarded operation that removes it from the queue. Older
# nodes keep their cancel/pause controls and simply omit the Edit button.
QUEUE_EDIT_CAPABILITY = "queue-edit"
# The switch route queues an engine change behind running/queued work instead
# of refusing it, and answers with the additive ``queued`` flag. Queue payloads
# then contain {kind:"engine"} rows beside {kind:"config"} ones.
QUEUED_ENGINE_SWITCH_CAPABILITY = "queued-engine-switch"
# Permission choices use the same ordered configuration rows as model/effort,
# and engine rows carry the target engine's permission default. Older nodes
# validate permission against the still-live engine and cannot safely accept a
# target-engine choice while a switch waits, so controllers gate that picker.
QUEUED_PERMISSION_CAPABILITY = "queued-permission-config"
# The session socket can hold automatic dequeue while a client rearranges the
# live queue, then atomically accept a revision-guarded permutation. Older
# nodes keep their ordinary queue and pause controls without draggable rows.
QUEUE_REORDER_CAPABILITY = "queue-reorder"
# The session snapshot carries a durable, versioned composer draft and the
# existing session socket accepts and broadcasts draft edits. Older remote
# nodes keep the console's local-only compatibility path.
SESSION_DRAFT_CAPABILITY = "session-drafts"
# The node exposes an active-turn compare token in session payloads and accepts
# authenticated POST /api/sessions/{sid}/steer with required expected_turn_id
# plus turn-scoped idempotency. Older nodes may carry an unadvertised transport
# preview; controllers must not offer steering without this hardened contract.
ACTIVE_TURN_STEERING_CAPABILITY = "active-turn-steering"
# Remote-workspace surfaces are two independent additive roles. A provider can
# lease one local project directory to its controller and serve the streamed
# manifest/fetch/apply protocol over it; a mirror host can run linked sessions
# whose engine works in a private synchronized copy behind per-turn sync
# barriers. Nodes never contact each other: every byte is brokered by the
# controller over the channels it already authenticates.
WORKSPACE_PROVIDER_CAPABILITY = "workspace-provider"
WORKSPACE_MIRROR_CAPABILITY = "workspace-mirror"
# A rebuilt mirror advertises a one-use reset marker in its manifest. A
# capable controller treats the authoritative workspace as the seed and
# acknowledges the marker only after a clean pull; older controllers are
# refused before they can mistake an empty rebuilt mirror for deletions.
WORKSPACE_MIRROR_RESET_CAPABILITY = "workspace-mirror-reset"
# One-shot spawned-agent execution: POST /api/spawn starts a single
# non-interactive engine run in a named directory on this node, and the
# job routes poll/cancel it. The controller relays a session's cross-node
# spawn requests here and never offers them to a node without this marker.
SPAWN_EXEC_CAPABILITY = "spawn-exec"
# Sliding recognized-progress leases plus PATCH /api/spawn/{job_id}, which lets
# the owning turn replace either live deadline during a run. Older spawn nodes
# retain their single fixed timeout and must not be offered limit updates.
SPAWN_LIMITS_CAPABILITY = "spawn-progress-limits"
# POST /api/spawn honors a controller-chosen job_id and is idempotent for it
# (the same id answered again returns the job it already started, and a poll
# or cancel for that id waits behind an in-flight start). A controller
# registers the relay handle before it transmits, so a start whose answer
# is lost can still be waited for, cancelled, and reaped; older nodes
# assign their own ids and an unanswered start there is reported as an error.
SPAWN_CLIENT_IDS_CAPABILITY = "spawn-client-job-ids"
# Ownership leases for relayed jobs: POST /api/spawn accepts lease_s, any
# poll or limit change renews it, and POST /api/spawn/renew renews a batch
# of ids (reporting unknown ones). A job whose controller stops renewing -
# a crash, a partition - is stopped by the node once the lease lapses
# instead of running unobserved to its own limits. Nodes without this keep
# today's behaviour; controllers without it never send lease_s.
SPAWN_OWNER_LEASE_CAPABILITY = "spawn-owner-lease"
# GET /api/search: full-history transcript search over this node's own
# sessions, answered from a node-local rebuildable FTS index. A console fans a
# query out to itself and to online nodes advertising this and merges results;
# offline or older nodes simply contribute nothing.
SEARCH_CAPABILITY = "session-search"
# GET /api/sessions/{sid}/events accepts an after_seq cursor and answers with
# the events that follow it, oldest first. A console uses it to load a window
# of history around one event (a search hit) instead of paging back to it
# from the newest; older nodes can only page backwards from the tail.
SESSION_EVENT_WINDOW_CAPABILITY = "session-event-window"
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
# PATCH /api/sessions/{sid} accepts a node-owned pinned flag and the session
# list keeps every pinned row above the ordinary activity/manual order. The
# same capability covers the hardened full-order compare contract used by
# drag-and-drop; older nodes retain their one unpinned order and no pin control.
SESSION_PINNING_CAPABILITY = "session-pinning"
# A node keeps a bounded, durable sequence of authoritative activity-block
# completions and serves GET /api/completions?after=<seq>. Controllers use it
# for remote completion commands without relying on an open browser.
COMPLETION_EVENTS_CAPABILITY = "completion-events"
# POST /api/sessions/{sid}/ask puts one question to the engine's own model
# alongside a running turn, without interrupting it or entering its
# conversation. Session payloads carry the additive side_question readiness
# object beside steering, and the answer arrives as its own transcript rows.
# Only engines whose pinned protocol has a native side-question request offer
# it; older nodes advertise nothing and consoles withhold the control.
SIDE_QUESTION_CAPABILITY = "active-turn-side-question"

SESSION_REFERENCES_CAPABILITY = "session-references"

BASE_CAPABILITIES = (
    "session-tasks",
    # Per-session tasks_enabled PATCH; disabling requires no child tasks.
    "session-tasks-toggle",
    SESSION_REFERENCES_CAPABILITY,
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
    ACTIVE_TURN_STEERING_CAPABILITY,
    SIDE_QUESTION_CAPABILITY,
    # model/effort changes made while work is pending hold their place in the
    # message queue ({kind:"config"} items) instead of applying immediately
    "queued-config",
    # engine switches requested while work is pending join the same queue as
    # additive {kind:"engine"} rows and apply in order; config rows also carry
    # the engine that validated them. Older nodes keep refusing mid-turn
    # switches with 409, and the console withholds the queued affordance.
    QUEUED_ENGINE_SWITCH_CAPABILITY,
    QUEUED_PERMISSION_CAPABILITY,
    "uploads",
    FILE_UPLOAD_CAPABILITY,
    "filesystem",
    "engine-switch",
    TEMPORARY_WORKSPACE_CAPABILITY,
    USAGE_REFRESH_CAPABILITY,
    MANUAL_USAGE_REFRESH_CAPABILITY,
    ENGINE_UPGRADE_CAPABILITY,
    BROWSER_CAPABILITY,
    BROWSER_INSTANCES_CAPABILITY,
    BROWSER_HANDOFF_CAPABILITY,
    BROWSER_FILE_WORKFLOWS_CAPABILITY,
    BROWSER_SHARED_STORAGE_CAPABILITY,
    UPLOAD_PREVIEW_CAPABILITY,
    ENGINE_AUTO_UPGRADE_CAPABILITY,
    ENGINE_DEFAULTS_CAPABILITY,
    TIMER_SETTINGS_CAPABILITY,
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
    SESSION_TOOLS_CAPABILITY,
    SESSION_FAST_MODE_CAPABILITY,
    SESSION_AGENT_NOTES_CAPABILITY,
    SESSION_PINNING_CAPABILITY,
    COMPLETION_EVENTS_CAPABILITY,
    NODE_STATE_STREAM_CAPABILITY,
    SESSION_CONTROL_WS_CAPABILITY,
)
TERMINAL_CAPABILITY = "terminal"
# Identified node-owned PTYs, their create/list/delete routes, and the
# ID-scoped viewer WebSocket. Older terminal-capable nodes keep the anonymous
# create-on-connect socket and remain usable without agent collaboration.
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

# Signed/staged remote upgrades are additive to protocol 1. A backend advertises
# this only while its external health-check/rollback launcher is active. The
# GET descriptor's readiness object is additive: older nodes omit it and still
# enforce their original POST-time idle gate.
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
