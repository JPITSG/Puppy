"""Questions an engine puts to the person mid-turn.

Claude Code's ``AskUserQuestion`` tool is a permission request like any
other: the CLI parks the tool call on a ``control_request``/``can_use_tool``
whose ``input`` holds the questions, and the host answers it with an
``allow`` whose ``updatedInput`` carries the person's ``answers``. Nothing
in Puppy's runner or console knows the tool by name: the driver marks such a
request ``kind: "question"`` with the rows this module makes of the input,
the console draws a form instead of the raw JSON card, and the answer travels
back through the ordinary approval reply as ``answers`` indexed by row.

Everything the CLI's own shape says, verified against claude 2.1.278:

- ``questions``: a list of ``{question, header, options, multiSelect}``, up to
  four, with ``header`` a short chip (12 characters) and ``options`` two to
  four ``{label, description, preview?}`` rows. Extended questions add
  ``kind`` (``choice`` | ``text`` | ``number``), ``description``,
  ``placeholder`` and, for a number, ``min``/``max``/``step``/``defaultValue``
  /``unit``; the whole call may carry a ``title``.
- ``answers``: a map from the question's own text to the answer - the picked
  label, several labels joined ``", "`` (a label holding ``", "`` or ``"`` is
  JSON-quoted first, the TUI's own ``kqe`` join), or the person's own words.
  A question left out is unanswered; ``"(notes only)"`` is the CLI's sentinel
  for a skipped one. The result the model reads is built by the CLI from that
  map: "Your questions have been answered: …" when every answer is one of the
  offered labels, "The user answered: … Read the answers carefully" when one
  is the person's own text, and "The user did not answer the questions." with
  none - which is what a bare allow used to hand it.

The reader is deliberately loose about the shape: renamed keys, string
options, a single question outside a list and unknown kinds are all read
rather than refused, because a card the person can answer beats a raw JSON
one, and the answer map is keyed by the question text exactly as the engine
sent it, which is the one thing the CLI matches on. A request this module
cannot read at all stays an ordinary approval.
"""
from __future__ import annotations

import json
import re

# bounds on what a card is asked to draw; the CLI itself allows four of each
QUESTION_LIMIT = 12
OPTION_LIMIT = 24
TEXT_LIMIT = 4000
LABEL_LIMIT = 400
PREVIEW_LIMIT = 20000
ANSWER_LIMIT = 4000
ANSWER_ITEMS = 64

# the names the tool has gone by, compared without case, separators or an
# MCP server prefix (``mcp__host__ask_user_question``)
TOOL_NAMES = frozenset({
    "askuserquestion", "askuserquestions", "askuser", "askquestion",
    "askquestions", "userquestion", "userquestions", "question", "questions",
})
KINDS = {
    "choice": "choice", "choices": "choice", "select": "choice",
    "single": "choice", "multi": "choice", "multiselect": "choice",
    "text": "text", "string": "text", "freeform": "text", "input": "text",
    "textarea": "text", "open": "text",
    "number": "number", "numeric": "number", "integer": "number",
    "int": "number", "float": "number",
}
_QUESTION_KEYS = ("question", "text", "prompt", "title", "label")
# a title beside the question text is its chip; alone it is the question
_HEADER_KEYS = ("header", "short", "chip", "tag", "title", "label")
_DESCRIPTION_KEYS = ("description", "help", "hint", "detail")
_OPTIONS_KEYS = ("options", "choices", "answers", "items")
_LABEL_KEYS = ("label", "value", "title", "text", "name")
_MULTI_KEYS = ("multiSelect", "multi_select", "multiselect", "multiple",
               "multi", "allowMultiple", "allow_multiple")
_KIND_KEYS = ("kind", "type", "input_type", "inputType")
_NUMBER_KEYS = (("min", ("min", "minimum")), ("max", ("max", "maximum")),
                ("step", ("step",)),
                ("default", ("defaultValue", "default_value", "default")))
SKIPPED = "(notes only)"


def _clean_name(name) -> str:
    text = str(name or "")
    if "__" in text:
        text = text.rsplit("__", 1)[-1]
    return re.sub(r"[^a-z0-9]", "", text.lower())


def is_question_tool(name) -> bool:
    """Whether a tool name is one of the spellings the question tool goes by."""
    return _clean_name(name) in TOOL_NAMES


def _text(value, limit):
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        value = json.dumps(value)
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value[:limit]


def _first(row: dict, keys, limit):
    for key in keys:
        text = _text(row.get(key), limit)
        if text:
            return text
    return ""


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return int(number) if number.is_integer() else number
    return None


def _options(row: dict) -> list:
    raw = None
    for key in _OPTIONS_KEYS:
        if isinstance(row.get(key), list):
            raw = row[key]
            break
    if raw is None:
        return []
    rows, seen = [], set()
    for entry in raw:
        if len(rows) >= OPTION_LIMIT:
            break
        if isinstance(entry, dict):
            label = _first(entry, _LABEL_KEYS, LABEL_LIMIT)
            description = _first(entry, _DESCRIPTION_KEYS, TEXT_LIMIT)
            preview = _text(entry.get("preview"), PREVIEW_LIMIT)
        else:
            label = _text(entry, LABEL_LIMIT)
            description = preview = ""
        if not label or label in seen:
            continue
        seen.add(label)
        option = {"label": label}
        if description:
            option["description"] = description
        if preview:
            option["preview"] = preview
        rows.append(option)
    return rows


def _question(entry, index: int):
    """One raw question entry -> the row a card draws, or None."""
    if isinstance(entry, str):
        entry = {"question": entry}
    if not isinstance(entry, dict):
        return None
    key = ""
    for name in _QUESTION_KEYS:
        value = entry.get(name)
        if isinstance(value, str) and value.strip():
            key = value
            break
    if not key:
        return None
    options = _options(entry)
    kind = KINDS.get(_clean_name(_first(entry, _KIND_KEYS, 40)), "")
    if not kind:
        kind = "choice" if options else "text"
    if kind == "choice" and not options:
        kind = "text"
    multi = any(entry.get(name) is True or str(entry.get(name) or "").lower()
                in ("true", "1", "yes") for name in _MULTI_KEYS)
    row = {
        "index": index,
        "key": key,
        "question": _text(key, TEXT_LIMIT),
        "header": next((_text(entry.get(name), LABEL_LIMIT) for name in _HEADER_KEYS
                        if _text(entry.get(name), LABEL_LIMIT) and entry.get(name) != key), ""),
        "description": _first(entry, _DESCRIPTION_KEYS, TEXT_LIMIT),
        "kind": kind,
        "multi": kind == "choice" and multi,
        "options": options if kind == "choice" else [],
    }
    placeholder = _text(entry.get("placeholder"), LABEL_LIMIT)
    if placeholder:
        row["placeholder"] = placeholder
    if kind == "number":
        for name, keys in _NUMBER_KEYS:
            for source in keys:
                number = _number(entry.get(source))
                if number is not None:
                    row[name] = number
                    break
        unit = _text(entry.get("unit"), LABEL_LIMIT)
        if unit:
            row["unit"] = unit
    return row


def questions_from(tool_name, tool_input):
    """The rows a request asks, or None when it is not a question at all.

    ``(rows, title)``: the request's questions as the card draws them - each
    keyed by the engine's own question text - and the call's optional title.
    The name alone does not decide: an input holding readable questions is a
    question whatever its tool is called, and a question tool whose input
    holds none stays an ordinary approval.
    """
    if not isinstance(tool_input, dict):
        return None
    raw = None
    if isinstance(tool_input.get("questions"), list):
        raw = tool_input["questions"]
    elif isinstance(tool_input.get("questions"), dict):
        raw = [tool_input["questions"]]
    elif is_question_tool(tool_name):
        # the looser spellings, read only under the tool's own name
        for key in ("prompts", "items"):
            if isinstance(tool_input.get(key), list):
                raw = tool_input[key]
                break
        if raw is None:
            single = tool_input.get("question")
            if isinstance(single, dict) and single:
                raw = [single]
            elif isinstance(single, str) and single.strip():
                raw = [tool_input]
            elif any(isinstance(tool_input.get(key), list) for key in _OPTIONS_KEYS):
                raw = [tool_input]
    if not raw:
        return None
    rows = []
    for entry in raw[:QUESTION_LIMIT]:
        row = _question(entry, len(rows))
        if row is not None:
            rows.append(row)
    if not rows:
        return None
    return rows, _text(tool_input.get("title"), TEXT_LIMIT)


def join_labels(labels) -> str:
    """Several picked labels as one answer, the way the CLI's own picker
    spells them: ``", "`` between, a label holding that separator or a
    quote JSON-quoted so the CLI can split them again."""
    return ", ".join(json.dumps(label) if (", " in label or '"' in label) else label
                     for label in labels)


def _answer_text(value):
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if not isinstance(value, str):
        return ""
    return value.strip()[:ANSWER_LIMIT]


def answer_map(rows, answers) -> dict:
    """The console's answers by row index -> the engine's map by question.

    A value is the chosen label, the person's own text, or for a multi-select
    row a list of labels (joined the CLI's way). A row not answered, an index
    the request never asked or a value that is not text is left out: the CLI
    reads an absent key as "unanswered", never as an error.
    """
    if not isinstance(answers, dict):
        return {}
    by_index = {}
    for row in rows or []:
        if isinstance(row, dict) and isinstance(row.get("index"), int) and \
                isinstance(row.get("key"), str) and row["key"]:
            by_index[row["index"]] = row
    result = {}
    for raw_index, value in answers.items():
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        row = by_index.get(index)
        if row is None:
            continue
        if isinstance(value, list):
            items = []
            for item in value[:ANSWER_ITEMS]:
                text = _answer_text(item)
                if text and text not in items:
                    items.append(text)
            if not items:
                continue
            answer = join_labels(items) if row.get("multi") else items[0]
        else:
            answer = _answer_text(value)
            if not answer:
                continue
        if answer == SKIPPED:
            continue
        result[row["key"]] = answer
    return result


def reply_input(original_input, rows, answers) -> dict:
    """The tool input an allow carries back: the engine's own input, with the
    answers the person gave. An empty map is a deliberate "no answer"."""
    updated = dict(original_input) if isinstance(original_input, dict) else {}
    updated["answers"] = answer_map(rows, answers)
    return updated


def describe(rows, answers) -> str:
    """One line naming the answers, for logs and status text."""
    given = answer_map(rows, answers)
    if not given:
        return "no answer"
    parts = []
    for row in rows or []:
        key = row.get("key") if isinstance(row, dict) else None
        if key in given:
            parts.append("{}: {}".format(row.get("header") or row.get("question") or key,
                                         given[key]))
    return "; ".join(parts)
