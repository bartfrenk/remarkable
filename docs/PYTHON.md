# Python Guidelines

General guidelines for any Python project. Keep this file free of project-specific rules.

Target Python 3.12+.

## Style

- Format with `black` and `isort` (isort `profile = "black"`), using the project's configured line length.
- Start modules with `from __future__ import annotations`.
- Use absolute imports (`from package.module import Name`), grouped stdlib / third-party / local.
- Give non-trivial modules a docstring explaining *why*, not *what*. Comment sparingly.

## Types

- Annotate all function signatures; code must pass `basedpyright` with no errors or warnings.
- Use builtin generics and `|` unions (`list[str]`, `str | None`).
- Use PEP 695 syntax for generics and aliases (`def first[T](xs: list[T]) -> T`, `type Json = ...`).
- Use `typing.Self` for methods returning their own instance.
- Prefer `@dataclass(frozen=True, slots=True)` for plain records, Pydantic models for parsed external data.
- Avoid `Any` except at untyped boundaries (e.g. raw JSON).

## I/O

- Prefer async for network calls (e.g. `aiohttp`, `httpx.AsyncClient`).

## Tests

- Use `pytest` with `pytest-asyncio` (auto mode); write plain `async def test_...`.
- Test through public APIs; mock at the network boundary, not internal functions.
- Prefer `polyfactory` factories over hand-built instances of dataclasses and Pydantic models.
