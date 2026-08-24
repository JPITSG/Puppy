"""Cross-engine handoff: render a session's normalized transcript into a context
preamble so a fresh native session on another engine can continue the work.
The filesystem state carries most of the real context; this carries the narrative."""
from __future__ import annotations

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
    if k == "error":
        return f"[error: {d.get('text', '')}]"
    return ""


def needs_handoff(session: dict) -> bool:
    """Fresh native session but existing meaningful history -> seed with handoff."""
    if session.get("native_session_id"):
        return False
    row = db.query_one(
        "SELECT id FROM events WHERE session_id=? AND kind IN ('assistant','tool_use') LIMIT 1",
        (session["id"],))
    return row is not None


def build(session: dict, exclude_seq=None) -> str:
    events = db.get_events(session["id"], limit=400)
    parts = []
    for ev in events:
        if exclude_seq is not None and ev["seq"] == exclude_seq:
            continue
        if ev["kind"] in ("user", "assistant", "tool_use", "tool_result", "engine_switch", "error"):
            s = _fmt(ev)
            if s:
                parts.append(s)

    body = "\n\n".join(parts)
    if len(body) > MAX_CHARS:
        head, tail = body[:2000], body[-(MAX_CHARS - 2200):]
        body = head + "\n\n[... earlier conversation truncated ...]\n\n" + tail

    return (
        "You are taking over an ongoing AI coding session. A previous agent worked in this "
        f"same working directory ({session['cwd']}); the current state of the files reflects "
        "its work. The conversation so far is transcribed below. Read it, then continue the "
        "session seamlessly - do not re-introduce yourself or redo completed work.\n\n"
        "----- PREVIOUS CONVERSATION -----\n\n"
        + body +
        "\n\n----- END OF PREVIOUS CONVERSATION -----\n\n"
        "The user's next message follows:\n\n"
    )
