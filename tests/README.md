# `tests/` — Test Suite

Pytest test suite for EviTrace. All tests are runnable from the repo
root with:

```bash
python -m pytest -q
```

`pytest`'s import mode and project root are configured in
[`pdf_extractor/pyproject.toml`](../pdf_extractor/README.md):

```toml
[tool.pytest.ini_options]
pythonpath = "."
addopts = "--import-mode=importlib -m \"not slow\""
markers = [
    "slow: marks tests as slow (deselected by default; run with -m slow or -m \"\")",
]
```

Slow tests are deselected by default. To include them:

```bash
python -m pytest -q -m slow      # only slow tests
python -m pytest -q -m ""        # everything
```

The root-level [`pdf_extractor/conftest.py`](../pdf_extractor/README.md)
ensures the project root is at the front of `sys.path` so that
`pdf_extractor.*` and `utils.*` resolve correctly during collection.

---

## Layout

```text
tests/
└── pdf_extractor/        Tests for the PDF extractor and quality_control modules
```

| Directory | Documentation |
| --------- | ------------- |
| `tests/pdf_extractor/` | [pdf_extractor/README.md](pdf_extractor/README.md) |

---

## Coverage at a glance

The current suite focuses on:

- `pdf_extractor` — extraction tier orchestration (`tier1`, `tier2`,
  `tier3`), schemas, branch backends, sentence/text utilities,
  embedding helpers, layout detection, and the parser pipeline.
- `quality_control` — the generic QC pipeline, models, local metrics,
  artifact generator, rater, IAA calculator, adjudicator, and
  reconciler.
- Cross-cutting concerns — logging utilities, source resolution,
  metrics-hierarchy tracking, and steering-drift regressions.

There are currently no dedicated tests for the `agents/openai`
package or the `pipeline/` orchestrator; both rely on integration
exercise via the parser/QC test cases plus manual end-to-end runs.

---

## Related

- Root overview: [../README.md](../README.md)
- Module under test: [../pdf_extractor/README.md](../pdf_extractor/README.md)
- Module under test: [../quality_control/README.md](../quality_control/README.md)
