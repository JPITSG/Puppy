"""Controller-local permanent short names for stable session references.

Each optional meta row is one reservation, including retired sessions. An
absent row is an unallocated code, never an older persisted shape. The meta
primary key and an immediate transaction serialize allocation across callers.
"""
from __future__ import annotations

import json
import re
import secrets
import unicodedata

from puppy import db

PREFIX = "session_alias."
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
CAPACITY = len(ALPHABET) ** 4
CODE_RE = re.compile(r"[A-Z0-9]{4}\Z")
REF_RE = re.compile(r"(?:all|[a-f0-9]{32}/[1-9][0-9]{0,15})\Z")
MENTION_RE = re.compile(r"(?<!\w)@Session-([\w-]+)-([A-Z0-9]{4})(?![\w/-])", re.I)


def _record(key, raw):
    code = key[len(PREFIX):]
    try:
        value = json.loads(raw)
        if not CODE_RE.fullmatch(code) or not isinstance(value, dict) or \
                set(value) != {"format", "ref"} or type(value["format"]) is not int or \
                value["format"] != 1 or not isinstance(value["ref"], str) or \
                not REF_RE.fullmatch(value["ref"]):
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise ValueError("session alias state is not current") from exc
    return code, value["ref"]


def reservations(connection):
    result, refs = {}, set()
    for row in connection.execute("SELECT key,value FROM meta WHERE key GLOB ?", (PREFIX + "*",)):
        code, ref = _record(row[0], row[1])
        if ref in refs:
            raise ValueError("session alias state contains duplicate references")
        refs.add(ref)
        result[code] = ref
    return result


def _random_code():
    return "".join(secrets.choice(ALPHABET) for _ in range(4))


def allocate(refs):
    """Return ref -> code, committing every new reservation atomically."""
    refs = list(dict.fromkeys(refs))
    if any(not isinstance(ref, str) or not REF_RE.fullmatch(ref) for ref in refs):
        raise ValueError("invalid session alias reference")
    with db._lock:
        connection = db.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            used = reservations(connection)
            assigned = {ref: code for code, ref in used.items()}
            for ref in refs:
                if ref in assigned:
                    continue
                if len(used) >= CAPACITY:
                    raise ValueError("all four-character session IDs are reserved")
                code = _random_code()
                for _ in range(64):
                    if code not in used:
                        break
                    code = _random_code()
                if code in used:
                    # Bounded fallback also works when almost every slot is used.
                    for number in range(CAPACITY):
                        digits = []
                        for _ in range(4):
                            number, digit = divmod(number, len(ALPHABET))
                            digits.append(ALPHABET[digit])
                        code = "".join(digits)
                        if code not in used:
                            break
                connection.execute("INSERT INTO meta(key,value) VALUES(?,?)", (
                    PREFIX + code, json.dumps({"format": 1, "ref": ref})))
                used[code], assigned[ref] = ref, code
            connection.commit()
            return {ref: assigned[ref] for ref in refs}
        except Exception:
            connection.rollback()
            raise


def resolve(codes):
    if not isinstance(codes, list) or not codes or len(codes) > 512:
        raise ValueError("select between 1 and 512 session IDs")
    refs = []
    for code in codes:
        if not isinstance(code, str) or not CODE_RE.fullmatch(code.upper()):
            raise ValueError("invalid four-character session ID")
        code = code.upper()
        row = db.query_one("SELECT key,value FROM meta WHERE key=?", (PREFIX + code,))
        if row is None:
            raise ValueError("unknown session ID {}; select it in the session picker".format(code))
        refs.append(_record(row["key"], row["value"])[1])
    return ["all"] if "all" in refs else list(dict.fromkeys(refs))


def mention(title, code):
    name = re.sub(r"[^\w]+", "-", unicodedata.normalize("NFKC", title)).strip("-")[:80].rstrip("-")
    return "@Session-{}-{}".format(name or "Session", code)
