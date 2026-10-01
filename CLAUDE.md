# CLAUDE.md

Async Python library and CLI (`remarkable`) for the unofficial reMarkable Cloud API.

- Check: `uv run --all-groups pytest`, `uv run --all-groups basedpyright`, `uvx black --check src tests`, `uvx isort --check src tests`.
- Keep README.md concise: essentials only, no implementation details.
- All HTTP goes through `SyncApi` in `src/remarkable/sync.py`.
