# Python Guidelines

Target Python 3.12+.

## Style

- Format with `black` and `isort` (line length 99).
- Start modules with `from __future__ import annotations`.
- Use absolute imports (`from remarkable.sync import SyncApi`), grouped stdlib / third-party / local.
- Give non-trivial modules a docstring explaining *why*, not *what*. Comment sparingly.

## Types

- Annotate all function signatures; code must pass `basedpyright` with no errors or warnings.
- Use builtin generics and `|` unions (`list[str]`, `str | None`).
- Use PEP 695 syntax for generics and aliases (`def first[T](xs: list[T]) -> T`, `type Json = ...`).
- Use `typing.Self` for methods returning their own instance.
- Prefer `@dataclass(frozen=True, slots=True)` for plain records, Pydantic models for parsed API data.
- Avoid `Any` except at untyped boundaries (e.g. raw JSON).

## Tests

- Use `pytest` with `pytest-asyncio` (auto mode); write plain `async def test_...`.
- Test through public APIs; mock at the HTTP boundary, not internal functions.
