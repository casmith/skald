# Contributing

Skald is standard-library Python. There is nothing to install but the test
tools.

```sh
pytest -q          # the suite, ~0.1s
ruff check .       # lint
python3 -m skald   # run it, with the env vars from the README
```

Two house rules:

- **No real player data, ever.** Fixtures use invented names, Steam IDs and
  worlds. The repository is public and this software watches when people are
  at their computers.
- **Bugs get a test first.** Every bug found so far came from a case nobody
  had thought to write down: a session whose leave was never logged, a death
  that reads like a join, a world whose bosses fell before Skald arrived.

`ruff format` is not enforced yet; the formatting is deliberate in places
(the inline CSS and HTML especially). Tightening that is a future ratchet,
one rule at a time.

The weather engine is a port of public-domain work and matches it exactly;
if you change it, the vectors in `tests/fixtures/weather_vectors.json` have
to still pass.
