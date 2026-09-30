# CLAUDE.md

Async Python library and CLI (`remarkable`) for the unofficial reMarkable Cloud API.

- Follow [docs/PYTHON.md](docs/PYTHON.md) for all Python code.
- Check: `uv run pytest`, `uvx basedpyright`, `uvx black --check src tests`, `uvx isort --check src tests`.
- Keep README.md concise: essentials only, no implementation details.
- All HTTP goes through `SyncApi` in `src/remarkable/sync.py`.
