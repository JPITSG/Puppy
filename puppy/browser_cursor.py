"""Read-only cursor hints for the streamed page, never arbitrary viewer CSS.

CDP has no native cursor-change event. Its hit test does pierce shadow roots
and in-process frames. Remote frames/native widgets can only be best effort;
image cursors use their CSS keyword fallback without fetching a page's URL
from the console. Nothing here changes the page, focus, or pointer position.
"""

CURSOR_KEYWORDS = frozenset("""
default none context-menu help pointer progress wait cell crosshair text
vertical-text alias copy move no-drop not-allowed grab grabbing all-scroll
col-resize row-resize n-resize e-resize s-resize w-resize ne-resize nw-resize
se-resize sw-resize ew-resize ns-resize nesw-resize nwse-resize zoom-in zoom-out
""".split())

CURSOR_FUNCTION = r"""function(x, y) {
  const el = this.nodeType === 1 ? this : this.parentElement;
  if (!el || !el.isConnected) return 'default';
  const doc = el.ownerDocument, win = doc.defaultView;
  const style = win.getComputedStyle(el);
  // A URL may itself contain commas: only the final keyword is relevant.
  const cursor = style.cursor.split(',').pop().trim();
  if (cursor !== 'auto') return cursor;
  if (el.matches(':disabled')) return 'default';
  if (el.isContentEditable) return style.writingMode.startsWith('vertical') ? 'vertical-text' : 'text';
  // CDP's coordinates are in the top viewport; caret APIs are document-local.
  try {
    for (let frame = win; frame !== frame.top; frame = frame.parent) {
      const owner = frame.frameElement;
      if (!owner) return 'default';
      const rect = owner.getBoundingClientRect();
      x -= rect.left + owner.clientLeft;
      y -= rect.top + owner.clientTop;
    }
    if (style.userSelect === 'none') return 'default';
    const caret = doc.caretRangeFromPoint(x, y);
    if (!caret || caret.startContainer.nodeType !== 3 ||
        !el.contains(caret.startContainer)) return 'default';
    const node = caret.startContainer, offset = caret.startOffset;
    const range = doc.createRange();
    range.setStart(node, Math.max(0, offset - 1));
    range.setEnd(node, Math.min(node.length, offset + 1));
    for (const rect of range.getClientRects()) {
      if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom)
        return style.writingMode.startsWith('vertical') ? 'vertical-text' : 'text';
    }
  } catch (_) {}
  return 'default';
}"""


async def read_cursor(call, fire, session, x, y, group):
    """One bounded probe; the caller owns scheduling and stale-reply checks."""
    try:
        hit = await call("DOM.getNodeForLocation", {
            "x": int(x), "y": int(y), "includeUserAgentShadowDOM": True,
        }, session=session)
        resolved = await call("DOM.resolveNode", {
            "backendNodeId": hit["backendNodeId"], "objectGroup": group,
        }, session=session)
        object_id = (resolved.get("object") or {}).get("objectId")
        if not object_id:
            return "default"
        result = await call("Runtime.callFunctionOn", {
            "objectId": object_id, "functionDeclaration": CURSOR_FUNCTION,
            "arguments": [{"value": x}, {"value": y}],
            "returnByValue": True, "awaitPromise": False,
        }, session=session)
        value = (result.get("result") or {}).get("value")
        return value if isinstance(value, str) and value in CURSOR_KEYWORDS else "default"
    finally:
        fire("Runtime.releaseObjectGroup", {"objectGroup": group}, session=session)
