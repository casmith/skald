"""Every page is a Python string, which keeps costing us.

An escape meant for the browser is read by Python first. `'\\n'` in the
script became a real newline and broke the whole thing; `\\2192` in a
stylesheet became octal 21 followed by "92", so an arrow turned into a
control character. Both looked fine in the source and both shipped.

A control character is the tell in either case, and nothing legitimate in
these pages contains one.
"""
import pytest

from skald import app

PAGES = [name for name in dir(app)
         if name.endswith("PAGE") and isinstance(getattr(app, name), str)]


def test_there_are_pages_to_check():
    assert PAGES, "no page templates found -- this test would pass on nothing"


@pytest.mark.parametrize("name", PAGES)
def test_no_page_holds_a_control_character(name):
    page = getattr(app, name)
    for n, line in enumerate(page.split("\n"), start=1):
        bad = [(i, ord(c)) for i, c in enumerate(line)
               if ord(c) < 32 and c != "\t"]
        assert not bad, (
            f"{name} line {n} holds control characters {bad}: {line.strip()!r}"
            " -- an escape meant for the browser was eaten by Python")
