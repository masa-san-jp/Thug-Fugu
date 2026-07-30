# Contributing to Thug-Fugu

## Development checks

Use Python 3.9+ and install the development extras:

```bash
python -m pip install '.[dev]'
```

Before opening a pull request:

```bash
python -m ruff check src tests
python -m ruff format --check src tests
PYTHONPATH=src python -m coverage run -m unittest discover -s tests -v
python -m coverage report --fail-under=85
python -m build
```

## Feature status and documentation

[`docs/audit/feature-inventory.md`](docs/audit/feature-inventory.md) is the
single source of truth for implementation status. Use these labels:

- `stable`: implemented and test-backed
- `experimental`: implemented but intentionally narrow/minimal
- `partial`: only a documented subset is implemented
- `not implemented`: design/tracking only
- `deprecated`: present but discouraged

When a pull request changes user-visible behavior:

1. Update or add tests that prove the behavior.
2. Update the matching README, design, operations, integration, or reference
   documentation.
3. Update the feature inventory if a row changes status, scope, evidence, or
   known gaps.
4. Update `CHANGELOG.md` for release-relevant behavior.
5. Keep statements about distributed inference precise: static remote endpoints
   and endpoint failover exist; registered-node clustering does not.

The pull request template is the required review-time consistency check. Reviewers
should reject a feature PR when code, tests, and documentation disagree.

## Safety

- Do not add arbitrary shell execution or network/file tools to the default tool
  registry.
- Preserve loopback-default server binding and redaction of sensitive data.
- New background threads, queues, and retries must be bounded and cleaned up in
  tests.
- Unsupported OpenAI-compatible behavior should fail explicitly rather than be
  silently accepted.
