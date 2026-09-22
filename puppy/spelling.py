"""The console's own dictionary: the words added to it, kept by the controller.

Controller-owned. Spell checking runs in the browser against the bundled word
list, but the words a person adds to it - by hand, through Add to dictionary,
or by the checker learning them - are shared by every console signed in to
this instance, so they live here in one exact-shape ``spelling`` meta record
rather than in each browser's storage. Every console learns the list through
the ``spelling`` state topic and reports the marked words of each message it
sends; this module keeps the tally behind the learning rule - a marked word
sent LEARN_SENDS times within LEARN_DAYS days joins the list by itself - so
five sends spread over the person's browsers add up. The headless backend has
no console and serves none of this.
"""
from __future__ import annotations

import json
import re
import time

from puppy import db, runner

META_KEY = "spelling"
API_PATH = "/api/spelling"
FORMAT = 1
LEARN_SENDS = 5          # a marked word sent this many times…
LEARN_DAYS = 7           # …within this many days joins the dictionary
TALLY_WORDS = 500        # the tally's bound: the longest-quiet words go first
MAX_WORDS = 5000         # the dictionary's bound
MAX_WORD = 64
REPORT_WORDS = 200       # the most one sent message may report
# The console's own notion of a word: letters (the accented Latin ones a
# dictionary word may carry) and the apostrophes that hold a contraction or a
# possessive together, in lower case as the list keeps them.
_LETTER = "a-zß-öø-ɏ"
_WORD_RE = re.compile("^[" + _LETTER + "]+(?:['’][" + _LETTER + "]+)*['’]?$")


class ShapeError(ValueError):
    """The persisted record is not the current shape; it is refused, not repaired."""


def normalize(value) -> str:
    """The list's own form of a word, or "" for anything that is not one."""
    if not isinstance(value, str):
        return ""
    word = value.strip().lower()
    if not word or len(word) > MAX_WORD or not _WORD_RE.match(word):
        return ""
    return word


def _valid_stamp(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_state(value):
    if not isinstance(value, dict) or set(value) != {"format", "words", "tally"} or \
            value["format"] != FORMAT or not isinstance(value["words"], list) or \
            not isinstance(value["tally"], dict):
        return None
    words = value["words"]
    if len(words) > MAX_WORDS or any(normalize(word) != word for word in words) or \
            words != sorted(set(words)):
        return None
    tally = value["tally"]
    if len(tally) > TALLY_WORDS:
        return None
    known = set(words)
    for word, stamps in tally.items():
        if normalize(word) != word or word in known or not isinstance(stamps, list) or \
                not stamps or len(stamps) >= LEARN_SENDS or \
                not all(_valid_stamp(at) for at in stamps) or stamps != sorted(stamps):
            return None
    return {"format": FORMAT, "words": list(words),
            "tally": {word: list(stamps) for word, stamps in tally.items()}}


def _new_state() -> dict:
    return {"format": FORMAT, "words": [], "tally": {}}


def _load(connection) -> dict:
    """The validated record, or a fresh one while none has been written."""
    row = connection.execute("SELECT value FROM meta WHERE key=?", (META_KEY,)).fetchone()
    if row is None:
        return _new_state()
    try:
        value = json.loads(row[0], parse_constant=lambda name: (_ for _ in ()).throw(
            ValueError("invalid JSON constant {}".format(name))))
    except (TypeError, ValueError):
        value = None
    state = _valid_state(value)
    if state is None:
        raise ShapeError("the spelling dictionary has an unsupported persisted shape")
    return state


def _store(connection, state: dict) -> None:
    connection.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (META_KEY, json.dumps(state)))


def validate_persisted(connection) -> None:
    """Reject a malformed record before startup or a restore, never repair it."""
    _load(connection)


def _payload(state: dict) -> dict:
    return {"type": "spelling", "words": list(state["words"]),
            "learn_sends": LEARN_SENDS, "learn_days": LEARN_DAYS}


def payload() -> dict:
    with db._lock:
        return _payload(_load(db.connect()))


def publish() -> dict:
    """Install the current list as the ``spelling`` state topic."""
    return runner.publish_state(payload())


def _change(edit):
    """Load, let ``edit`` change the record, store what changed, publish.

    One immediate transaction serializes consoles writing at the same moment;
    ``edit`` returns what the caller wants to know beyond the list, and None
    when nothing changed, in which case nothing is written.
    """
    with db._lock:
        connection = db.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            state = _load(connection)
            result = edit(state)
            if result is not None:
                _store(connection, state)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    # An unchanged list keeps its revision: publishing the same payload again
    # is how a console that asked over HTTP learns nothing moved.
    return runner.publish_state(_payload(state)), result


def add(word) -> dict:
    """Add to dictionary: the word joins the list, and its tally is done with."""
    value = normalize(word)
    if not value:
        raise ValueError("not a word the dictionary can hold")

    def edit(state):
        if value in state["words"]:
            return None
        state["words"] = sorted(set(state["words"]) | {value})
        if len(state["words"]) > MAX_WORDS:
            raise ValueError("the dictionary holds {} words at most".format(MAX_WORDS))
        state["tally"].pop(value, None)
        return True

    published, _ = _change(edit)
    return published


def remove(word) -> dict:
    """Remove from dictionary: the word is marked again, and its tally starts over."""
    value = normalize(word)
    if not value:
        raise ValueError("not a word the dictionary can hold")

    def edit(state):
        if value not in state["words"]:
            return None
        state["words"] = [item for item in state["words"] if item != value]
        state["tally"].pop(value, None)
        return True

    published, _ = _change(edit)
    return published


def sent(words) -> dict:
    """A message was sent with these marked words: count each once, learn.

    Sends older than the window fall off first; a word then counted
    LEARN_SENDS times joins the list and is named in the answer's ``learned``.
    A word the list already holds (another console added it meanwhile) is
    not counted. The tally keeps at most TALLY_WORDS words, the longest quiet
    let go first.
    """
    if not isinstance(words, list) or len(words) > REPORT_WORDS:
        raise ValueError("supply at most {} words".format(REPORT_WORDS))
    reported = []
    for word in words:
        value = normalize(word)
        if not value:
            raise ValueError("not a word the dictionary can hold")
        if value not in reported:
            reported.append(value)
    now = int(time.time())
    since = now - LEARN_DAYS * 86400

    def edit(state):
        tally = state["tally"]
        changed = False
        for word in list(tally):
            fresh = [at for at in tally[word] if at >= since]
            if len(fresh) != len(tally[word]):
                changed = True
                if fresh:
                    tally[word] = fresh
                else:
                    del tally[word]
        learned = []
        known = set(state["words"])
        for word in reported:
            if word in known:
                continue
            changed = True
            stamps = tally.get(word, []) + [now]
            if len(stamps) < LEARN_SENDS:
                tally[word] = stamps
                continue
            tally.pop(word, None)
            known.add(word)
            learned.append(word)
        if learned:
            state["words"] = sorted(known)
        if len(tally) > TALLY_WORDS:
            for word in sorted(tally, key=lambda item: tally[item][-1])[:len(tally) - TALLY_WORDS]:
                del tally[word]
        return learned if changed else None

    published, learned = _change(edit)
    return dict(published, learned=list(learned or []))
