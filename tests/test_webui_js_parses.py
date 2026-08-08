"""THE PARSE GATE — the emitted client script must be valid JavaScript.

WHY THIS FILE EXISTS, and it is the most expensive lesson in this batch: NL-17 M1
shipped a `webui.JS` that did not parse. A comment insertion landed after the
pre-existing block's closing `*/`, so six lines of English prose became
top-level JavaScript. One SyntaxError kills the WHOLE 65KB script — not one
function, all of them — so every reader interaction on every surface was dead in
a real browser: follow, unfollow, swap, settle, even view switching.

IT SHIPPED PAST 3442 GREEN TESTS, and the reason is structural rather than
careless: this suite runs no JS engine, and every client pin reads
COMMENT-STRIPPED source text (`_js_code`). A defect that lives inside a comment
delimiter is invisible to a checker that deletes comment delimiters first. The
pins were all correct and all blind. QA's real-browser pass is what caught it.

So the gate is deliberately TWO teeth with different blind spots:

  1. `node --check` — a real parser, the only thing that proves "this is
     JavaScript". SKIPPED when node is absent, because requiring a Node install
     to run the Python suite is a dependency this project has not taken.
  2. THE DELIMITER SCAN — pure Python, always runs, and aimed squarely at the
     class that actually shipped: a `*/` appearing where no comment is open, or
     a `/*` never closed. It is not a parser and does not pretend to be; it
     catches unbalanced comment delimiters and nothing else. That bound is
     stated rather than hidden, because tooth 1 is the one that can be absent
     and tooth 2 is what remains when it is.

Both teeth are born red against the shipped-broken state (fix loop 2 receipts).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from newslens import webui


def _delimiter_faults(src: str):
    """Every unbalanced block-comment delimiter, as (line_no, kind).

    ONE state machine over the source — code / block comment / line comment /
    string — because the two-pass version (strip strings, then scan) is WRONG
    and this file's first draft proved it: stripping strings first treats a
    quotation mark INSIDE a comment as a string opener, swallows the rest of the
    comment including its `*/`, and reports a false "unclosed /*". It flagged a
    healthy file on its first run. Quotes only mean string when the scanner is
    in CODE.

    REGEX LITERALS ARE RECOGNISED, and skipping them is not optional — the
    first draft left them out with a note saying the worst case would be a loud
    false positive. It was not. `flEsc` contains `.replace(/"/g, …)`, whose bare
    `"` looked like a string opener; the scanner swallowed the rest of the file
    and reported the SHIPPED-BROKEN script as clean. A quiet false negative, in
    the one tooth that exists to catch what a comment-blind checker cannot —
    caught only because this file's own born-red leg measured green when it had
    to be red. The `/"/g` case is pinned below as a regression.

    Regex-vs-division uses the standard preceding-token heuristic: a `/` after a
    value (identifier, literal, `)`, `]`) is division; after an operator or
    opener it starts a literal.
    """
    faults, i, n = [], 0, len(src)
    state, quote, open_line = "code", "", None
    prev = ""          # last significant character seen in code
    kw = ("return", "typeof", "case", "in", "of", "new", "delete", "void",
          "instanceof", "do", "else", "yield")

    def _regex_here(idx: int) -> bool:
        j = idx - 1
        while j >= 0 and src[j] in " \t\n\r":
            j -= 1
        if j < 0 or src[j] in "(,=:[!&|?{};+-*%~^<>":
            return True
        k = j
        while k >= 0 and (src[k].isalnum() or src[k] == "_"):
            k -= 1
        return src[k + 1:j + 1] in kw

    while i < n:
        ch, pair = src[i], src[i:i + 2]
        if state == "code":
            if pair == "/*":
                state, open_line = "block", src.count("\n", 0, i) + 1
                i += 2
                continue
            if pair == "//":
                state = "line"
                i += 2
                continue
            if pair == "*/":
                faults.append((src.count("\n", 0, i) + 1, "stray */"))
                i += 2
                continue
            if ch == "/" and _regex_here(i):
                # skip the literal: /…/flags, honouring \ escapes and [ ] classes
                i += 1
                in_class = False
                while i < n:
                    c = src[i]
                    if c == "\\":
                        i += 2
                        continue
                    if c == "[":
                        in_class = True
                    elif c == "]":
                        in_class = False
                    elif c == "/" and not in_class:
                        i += 1
                        break
                    elif c == "\n":
                        break          # not a literal after all; bail safely
                    i += 1
                while i < n and src[i].isalpha():
                    i += 1
                continue
            if ch in "'\"`":
                state, quote = "string", ch
            i += 1
            continue
        if state == "block":
            if pair == "*/":
                state, open_line = "code", None
                i += 2
                continue
            i += 1
            continue
        if state == "line":
            if ch == "\n":
                state = "code"
            i += 1
            continue
        # string
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            state = "code"
        i += 1
    if state == "block":
        faults.append((open_line, "unclosed /*"))
    return faults


def test_the_emitted_client_script_has_balanced_comment_delimiters():
    """BORN RED against the shipped-broken state. THE ALWAYS-ON TOOTH.

    A stray `*/` is exactly the defect that shipped: the prose after it was
    parsed as code. This scan finds it with no JS engine, so the gate still
    bites on a machine with no node."""
    faults = _delimiter_faults(webui.JS)
    assert faults == [], (
        "unbalanced block-comment delimiters in the emitted client script — "
        "everything after a stray `*/` is parsed as CODE: " + repr(faults))


def test_the_emitted_client_script_parses_as_javascript():
    """BORN RED against the shipped-broken state. THE REAL PARSER, when there is
    one. Skipped rather than faked when node is absent — a gate that pretends to
    have checked is worse than one that says it could not."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available — the delimiter scan above still binds")
    with tempfile.TemporaryDirectory() as d:
        js = Path(d) / "emitted.js"
        js.write_text(webui.JS, encoding="utf-8")
        proc = subprocess.run([node, "--check", str(js)],
                              capture_output=True, text=True)
    assert proc.returncode == 0, (
        "the emitted client script does not parse:\n" + proc.stderr)


def test_the_scanner_actually_detects_the_defect_that_shipped():
    """THE SCANNER'S OWN BITE RECEIPT — a pin whose red has never been observed
    is not proof-class currency (ENGINEERING.md). This reconstructs the exact
    shipped defect (a `*/` closing a block early, leaving prose at top level)
    and proves the scanner reports it, so tooth 2 is known to work rather than
    merely known to pass."""
    broken = "/* a comment. */\n   MORE PROSE that is now code. */\nvar x = 1;\n"
    faults = _delimiter_faults(broken)
    assert faults and faults[0][1] == "stray */", faults
    # …and it does not cry wolf on the lawful shapes it must live beside.
    for ok in ("/* one */ var a = 1;",
               "var s = '*/ inside a string';",
               "// a line comment with */ in it\nvar b = 2;",
               '/* nested-looking /* but JS has no nesting */ var c = 3;',
               # THE REGRESSION THAT DISABLED THIS TOOTH (see _delimiter_faults):
               # flEsc's own regex. Its bare quote read as a string opener and
               # desynchronised the scanner over the whole file, so the shipped
               # BROKEN script measured clean.
               '''s.replace(/"/g, '&quot;').replace(/'/g, "&#39;"); /* c */''',
               "var re = /[^()]*\\\\)$/; var d = a / b / c;",
               "var e = x / 2; /* division, not a literal */"):
        assert _delimiter_faults(ok) == [], ok
