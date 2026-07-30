# Release process

This project uses semantic version tags such as `v0.1.0`.

## Checklist

1. Start from a clean, up-to-date `main`.
2. Update the version in `pyproject.toml`.
3. Add the dated release section to `CHANGELOG.md`.
4. Add release notes under `docs/releases/`.
5. Run the same checks as CI:

   ```bash
   python -m ruff check src tests
   python -m ruff format --check src tests
   PYTHONPATH=src python -m coverage run -m unittest discover -s tests -v
   python -m coverage report --fail-under=85
   python -m build
   ```

6. Merge the release-preparation pull request.
7. Create and push an annotated tag from the resulting `main` commit:

   ```bash
   git tag -a v0.1.0 -m "Thug-Fugu v0.1.0"
   git push origin v0.1.0
   ```

8. Create the GitHub release using the matching file under `docs/releases/`.

Do not tag a feature branch or a commit whose package version and changelog do
not match the tag.
