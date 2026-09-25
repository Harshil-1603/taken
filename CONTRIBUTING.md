# Contributing to taken?

Thanks for stopping by. A few notes before you open a PR.

- Keep the runtime stdlib-only. Dev tools (ruff, pytest) live in the uv dev
  group; add new ones there, never as runtime dependencies.
- Run `uv run ruff check`, `uv run ruff format --check`, and `uv run pytest`
  before pushing. CI runs the same three commands.
- The tool is read-only by design. It must never write anything to the GitHub
  API: no comments, no labels, no state changes, only GET requests.
- Match the existing style: small functions, plain names, no cleverness. The
  verdict logic in `taken/verdict.py` should stay easy to audit.
- If you change verdict behavior, update the tests in `tests/` and the "How
  the verdict works" section of the README.
- One concern per PR keeps reviews fast.
