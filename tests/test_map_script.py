"""The map page's script is a Python string, which is a trap.

An escape that is right in Python is not always right in JavaScript. Writing
`split('\\n')` in the page source gives the browser a string literal with a
real newline inside it, which is a syntax error -- and one bad token stops
the whole script, so the map loses its panning and zooming as well as
whatever was being added at the time. It is invisible from the server: the
page still returns 200.
"""
import re
import shutil
import subprocess

import pytest

from skald.app import MAP_PAGE


def _script():
    m = re.search(r"<script>(.*?)</script>", MAP_PAGE, re.S)
    assert m, "the map page has no script"
    return m.group(1)


def test_no_string_literal_runs_off_the_end_of_its_line():
    """The cheap version of a parser, and the one that catches the mistake
    that prompted this: a quote opened and never closed on the same line.

    Scanned rather than pattern-matched, because a backslash only escapes
    the character after it -- so the quote ending `'\\n'` is a real quote,
    and a rule that looks only at the previous character calls it escaped
    and then reports every such line as broken.

    It does not understand regular expression literals, where a quote is
    just a character. The script avoids them; `node --check` below is the
    parser that actually knows the language.
    """
    for n, line in enumerate(_script().splitlines(), start=1):
        quote, escaped, comment = None, False, False
        for i, ch in enumerate(line):
            if escaped:
                escaped = False
                continue
            if ch == "\\" and quote:
                escaped = True
            elif quote:
                if ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
            elif ch == "/" and line[i + 1:i + 2] == "/":
                comment = True
                break
        assert quote is None or comment, (
            f"line {n} leaves a {quote} open: {line.strip()!r}")


def test_braces_and_brackets_balance():
    js = _script()
    for opener, closer in (("(", ")"), ("{", "}"), ("[", "]")):
        assert js.count(opener) == js.count(closer), f"{opener}{closer} unbalanced"


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_real_parser_agrees(tmp_path):
    path = tmp_path / "map.js"
    path.write_text(_script())
    done = subprocess.run(["node", "--check", str(path)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_script_still_does_its_two_jobs():
    """A guard on the guard: these tests would all pass on an empty script."""
    js = _script()
    assert "addEventListener('wheel'" in js
    assert "dataset.names" in js
