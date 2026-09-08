#!/usr/bin/env python3
"""Build the bundled console spelling dictionary from a SCOWL release.

Puppy's spell checker is entirely its own: no browser dictionary, no network
service, no new runtime dependency. The word list it consults is generated
here, checked into the repository under ``puppy/static/dict/`` and served to
the console like any other authenticated static asset.

    python3 tools/build_dictionary.py            # download the pinned release
    python3 tools/build_dictionary.py --scowl <dir-or-tarball>

The source is SCOWL (Spell Checker Oriented Word Lists) by Kevin Atkinson,
the same collection the aspell/hunspell English dictionaries are cut from.
The release is pinned by version AND SHA-256: a build that cannot verify its
input writes nothing.

What lands in the asset:

  * the English, American, British and Canadian ``words``, ``upper``,
    ``contractions`` and ``abbreviations`` lists up to SCOWL size 60 (the
    size an ordinary desktop spell checker ships), plus ``proper-names`` up
    to size 50 so ordinary names are not flagged;
  * the unaccented spelling of every accented entry ("cafe" beside "café"),
    which is how most people type them and how American English prints them;
  * a small hand-kept supplement (``SUPPLEMENT`` below) of terms this console
    is typed at every day that SCOWL predates or omits;
  * a rank tag on the words common enough to be an autocorrect target, so the
    console can prefer "the" over "tea" and refuse to correct anything into an
    obscure word.

The file format is exact and versioned; the console rejects anything else
rather than tolerating an older shape:

    #puppy-dictionary 1 en
    #source ...                (provenance/licence lines, ignored by readers)
    #words <count>
    <word>[\t<rank digit>]     one per line, sorted by code point

A rank digit is an index into RANK_SIZES; an untagged word is known but never
suggested first and never used as an autocorrect target.
"""

import argparse
import gzip
import hashlib
import io
import os
import sys
import tarfile
import unicodedata
import urllib.request

SCOWL_VERSION = "2020.12.07"
SCOWL_URL = ("https://downloads.sourceforge.net/wordlist/"
             "scowl-%s.tar.gz" % SCOWL_VERSION)
SCOWL_SHA256 = "5587667caa20c4891390c2d42dbb4d5c4c3f41bee77af1457ece3ba23fb859cc"

# SCOWL splits every category by size class: 10 is the thousand commonest
# words, 95 is everything anyone ever wrote down. 60 is the ordinary desktop
# spelling dictionary; past it the list starts accepting words that are more
# often somebody's typo than their intent.
MAX_SIZE = 60
MAX_NAME_SIZE = 50
# A word from these size classes may be offered first and corrected into.
RANK_SIZES = (10, 20, 35, 40, 50)

LANGUAGES = ("english", "american", "british", "british_z", "canadian",
             "british_variant_1", "british_variant_2",
             "canadian_variant_1", "canadian_variant_2")
CATEGORIES = ("words", "upper", "contractions", "abbreviations", "proper-names")
SIZES = (10, 20, 35, 40, 50, 55, 60, 70, 80, 95)

# Words this console is typed at daily that a 2020 general-English list does
# not carry. Kept short and deliberately ordinary: a word added here stops
# being a typo everywhere, so nothing ambiguous belongs in it.
SUPPLEMENT = """
    agentic api apis async autocomplete autocorrect autocompletion await
    backend backends blockquote boolean bot bots browserless callback callbacks
    changelog chatbot cli config configs cron dev devs docstring dropdown
    emoji endpoint endpoints env favicon filesystem frontend gzip hostname
    hotkey http https init inline installable integer json kanban kubernetes
    lint linter linting login logout markdown middleware misconfigured mixin
    modal namespace namespaces nullable oauth offline onboarding param params
    parsers passphrase pipelining plaintext plugin plugins prepend prepends
    prepopulate preprocess prompted queueing rebase rebased refactor refactored
    refactoring regex regexes rekey rename renames repo repos rerun reruns
    runtime sandbox sandboxed screenshotted scroller sidebar spellcheck sql
    ssh stderr stdin stdout subcommand subdirectory subfolder submodule
    superuser sysadmin templating terminals timestamped todo tooltip traceback
    truthy typo typos ui unarchive uncheck unchecked undock unfocus unindent
    uninstall unpin unpinned untick upstream url urls username usernames uuid
    validator viewport vpn webhook webhooks websocket websockets whitespace
    wifi workflow workspace workspaces yaml zoomable ok
"""


def scowl_lists(root):
    """Every SCOWL ``final`` list this build wants, as (path, size, category)."""
    final = os.path.join(root, "final")
    if not os.path.isdir(final):
        raise SystemExit("not a SCOWL tree (no final/ directory): %s" % root)
    wanted = []
    for language in LANGUAGES:
        for category in CATEGORIES:
            limit = MAX_NAME_SIZE if category == "proper-names" else MAX_SIZE
            for size in SIZES:
                if size > limit:
                    continue
                path = os.path.join(final, "%s-%s.%d" % (language, category, size))
                if os.path.exists(path):
                    wanted.append((path, size, category))
    if not wanted:
        raise SystemExit("no SCOWL word lists found under %s" % final)
    return wanted


def clean(word):
    """A SCOWL entry as a token the console can actually meet, or ''.

    The console splits prose on everything but letters and apostrophes, so an
    entry with a space or a hyphen could never match one of its tokens; an
    abbreviation keeps its letters and loses its dots ("Mr." -> "Mr").
    """
    word = word.strip().replace("’", "'")
    if not word:
        return ""
    word = word.rstrip(".")
    if not word or any(c.isdigit() or c.isspace() or c in "-./\\" for c in word):
        return ""
    if word.strip("'") == "":
        return ""
    return word


def build_words(root):
    """{word: rank digit or None} for the whole bundled dictionary."""
    best = {}                      # word -> smallest size class seen
    ranked = set()                 # words allowed to be a correction target
    for path, size, category in scowl_lists(root):
        with open(path, "r", encoding="latin-1") as handle:
            for line in handle:
                word = clean(line)
                if not word:
                    continue
                if best.get(word, 999) > size:
                    best[word] = size
                if category != "proper-names" and size in RANK_SIZES:
                    ranked.add(word)
    # "café" is also typed "cafe", and printed that way in American English.
    plain = 0
    for word, size in list(best.items()):
        if word.isascii():
            continue
        bare = "".join(c for c in unicodedata.normalize("NFD", word)
                       if not unicodedata.combining(c))
        if not bare.isascii() or bare == word or bare in best:
            continue
        best[bare] = size
        plain += 1
        if word in ranked:
            ranked.add(bare)
    supplement = 0
    for word in SUPPLEMENT.split():
        if word not in best:
            supplement += 1
        best.setdefault(word, RANK_SIZES[-1])
        ranked.add(word)
    words = {}
    for word, size in best.items():
        rank = None
        if word in ranked and size in RANK_SIZES:
            rank = RANK_SIZES.index(size)
        words[word] = rank
    return words, supplement, plain


def render(words):
    out = io.StringIO()
    out.write("#puppy-dictionary 1 en\n")
    out.write("#source SCOWL %s (Spell Checker Oriented Word Lists), "
              "Copyright 2000-2020 Kevin Atkinson\n" % SCOWL_VERSION)
    out.write("#source see puppy/static/dict/COPYRIGHT for the full notice\n")
    out.write("#built tools/build_dictionary.py\n")
    out.write("#words %d\n" % len(words))
    for word in sorted(words):
        rank = words[word]
        out.write(word if rank is None else "%s\t%d" % (word, rank))
        out.write("\n")
    return out.getvalue()


def fetch(url):
    print("downloading %s" % url)
    request = urllib.request.Request(url, headers={"User-Agent": "puppy-dictionary-build"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scowl", default="",
                        help="an unpacked SCOWL tree or its release tarball; "
                             "omitted downloads the pinned release")
    parser.add_argument("--out", default=os.path.join(here, "puppy", "static", "dict"),
                        help="output directory (default puppy/static/dict)")
    args = parser.parse_args()

    work = None
    root = args.scowl
    if root and os.path.isdir(root):
        pass
    else:
        if root:
            with open(root, "rb") as handle:
                blob = handle.read()
        else:
            blob = fetch(SCOWL_URL)
        digest = hashlib.sha256(blob).hexdigest()
        if digest != SCOWL_SHA256:
            raise SystemExit("SCOWL checksum mismatch: expected %s, got %s"
                             % (SCOWL_SHA256, digest))
        import tempfile
        work = tempfile.mkdtemp(prefix="scowl-")
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
            for member in archive.getmembers():
                name = os.path.normpath(member.name)
                if name.startswith(("/", "..")) or not member.isreg():
                    continue
                archive.extract(member, work)
        entries = [os.path.join(work, name) for name in os.listdir(work)]
        roots = [path for path in entries if os.path.isdir(path)]
        root = roots[0] if len(roots) == 1 else work

    words, supplement, plain = build_words(root)
    text = render(words)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "en.txt")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    raw = text.encode("utf-8")
    # aiohttp serves this sibling to every browser that accepts gzip, so the
    # console pays ~1/4 of the transfer for the same asset.
    with open(path + ".gz", "wb") as handle:
        handle.write(gzip.compress(raw, 9, mtime=0))
    copyright_src = os.path.join(root, "Copyright")
    if os.path.exists(copyright_src):
        with open(copyright_src, "r", encoding="latin-1") as source:
            notice = source.read()
        with open(os.path.join(args.out, "COPYRIGHT"), "w", encoding="utf-8",
                  newline="\n") as handle:
            handle.write(notice)
    ranked = sum(1 for rank in words.values() if rank is not None)
    print("%s: %d words (%d ranked, %d unaccented, %d from the supplement), "
          "%.1f KB, %.1f KB gzipped"
          % (path, len(words), ranked, plain, supplement, len(raw) / 1024,
             os.path.getsize(path + ".gz") / 1024))
    if work:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
