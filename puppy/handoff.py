"""Cross-engine handoff: render a session's normalized transcript into a context
preamble so a fresh native session on another engine can continue the work.
The filesystem state carries most of the real context; this carries the narrative."""
from __future__ import annotations

import json

from puppy import db

MAX_CHARS = 16000


def _fmt(ev) -> str:
    k = ev["kind"]
    d = ev["data"]
    if k == "user":
        return f"## User\n{d.get('text', '')}"
    if k == "assistant":
        return f"## Assistant\n{d.get('text', '')}"
    if k == "tool_use":
        inp = str(d.get("input", {}))
        if len(inp) > 300:
            inp = inp[:300] + "..."
        return f"[tool call: {d.get('tool', '?')} {inp}]"
    if k == "tool_result":
        content = (d.get("content") or "").strip()
        if len(content) > 400:
            content = content[:400] + "..."
        prefix = "tool error" if d.get("is_error") else "tool result"
        return f"[{prefix}: {content}]" if content else ""
    if k == "engine_switch":
        return f"[session was moved from {d.get('from', '?')} to {d.get('to', '?')}]"
    if k == "info" and d.get("subtype") == "workspace_reset":
        return ("[scratch workspace reset: the previous workspace files are unavailable; "
                "this is a new empty workspace]")
    if k == "info" and d.get("subtype") in ("workspace_move", "context_reset"):
        return "[{}]".format(d.get("text", ""))
    if k == "info" and d.get("subtype") == "session_task_archive":
        # A removed task's conversation, folded into this transcript: its
        # outcome, what it wrote into the project and its final answer. The
        # condensed exchange itself is for the console; the preamble's own
        # cap decides how much of the narrative survives.
        from puppy import session_tasks
        files = str(d.get("applied_files") or "").splitlines()
        shown = "; ".join(line.split("\t")[-1] for line in files[:20])
        if len(files) > 20:
            shown += "; ... {} more".format(len(files) - 20)
        summary = str(d.get("summary") or "")
        if len(summary) > 2000:
            summary = summary[:2000] + "..."
        return "[folded task \"{}\": {}{}{}]".format(
            d.get("name") or "Task", session_tasks.state_phrase(d.get("state")),
            "; files applied to Main: " + shown if shown else "",
            "; final answer: " + summary if summary else "")
    if k == "error":
        return f"[error: {d.get('text', '')}]"
    return ""


def needs_handoff(session: dict) -> bool:
    """Fresh native session but existing meaningful history -> seed with handoff."""
    if session.get("native_session_id"):
        return False
    if db.query_one(
            "SELECT id FROM events WHERE session_id=? "
            "AND kind IN ('assistant','tool_use') LIMIT 1", (session["id"],)):
        return True
    rows = db.query(
        "SELECT payload FROM events WHERE session_id=? AND kind='info' "
        "ORDER BY seq DESC LIMIT 50", (session["id"],))
    for row in rows:
        try:
            if json.loads(row["payload"]).get("subtype") == "workspace_reset":
                return True
        except Exception:
            pass
    return False


def build(session: dict, exclude_seq=None) -> str:
    events = db.get_events(session["id"], limit=400)
    parts = []
    for ev in events:
        if exclude_seq is not None and ev["seq"] == exclude_seq:
            continue
        if ev["kind"] in ("user", "assistant", "tool_use", "tool_result",
                          "engine_switch", "info", "error"):
            s = _fmt(ev)
            if s:
                parts.append(s)

    body = "\n\n".join(parts)
    if len(body) > MAX_CHARS:
        head, tail = body[:2000], body[-(MAX_CHARS - 2200):]
        body = head + "\n\n[... earlier conversation truncated ...]\n\n" + tail

    return (
        "You are taking over an ongoing AI coding session. A previous agent worked in this "
        f"same working directory ({session['cwd']}); the filesystem may carry its work unless "
        "a lifecycle notice below says the workspace was reset. The conversation so far is "
        "transcribed below. Read it, then continue the "
        "session seamlessly - do not re-introduce yourself or redo completed work.\n\n"
        "----- PREVIOUS CONVERSATION -----\n\n"
        + body +
        "\n\n----- END OF PREVIOUS CONVERSATION -----\n\n"
        "The user's next message follows:\n\n"
    )
