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
# One-shot spawned-agent execution: POST /api/spawn starts a single
# non-interactive engine run in a named directory on this node, and the
# job routes poll/cancel it. The controller relays a session's cross-node
# spawn requests here and never offers them to a node without this marker.
SPAWN_EXEC_CAPABILITY = "spawn-exec"
# Sliding recognized-progress leases plus PATCH /api/spawn/{job_id}, which lets
# the owning turn replace either live deadline during a run. Older spawn nodes
# retain their single fixed timeout and must not be offered limit updates.
SPAWN_LIMITS_CAPABILITY = "spawn-progress-limits"

BASE_CAPABILITIES = (
    "sessions",
    "session-stream",
    "approvals",
    "message-queue",
    QUEUE_PAUSE_CAPABILITY,
    QUEUE_EDIT_CAPABILITY,
    QUEUE_REORDER_CAPABILITY,
    SESSION_DRAFT_CAPABILITY,
    ACTIVE_TURN_STEERING_CAPABILITY,
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
    SYSTEM_PROMPT_CAPABILITY,
    WORKSPACE_PROVIDER_CAPABILITY,
    WORKSPACE_MIRROR_CAPABILITY,
    SPAWN_EXEC_CAPABILITY,
    SPAWN_LIMITS_CAPABILITY,
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
