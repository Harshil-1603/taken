# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.1.1] - 2026-09-26

### Added
- Published to PyPI as `taken-gh` via trusted publishing (GitHub Actions
  OIDC). Install with `uv tool install taken-gh` or `pipx install taken-gh`;
  README and the project page now point at PyPI instead of a git URL.

## [Unreleased]

### Fixed
- Fail closed: every GitHub API check now validates the shape of the
  response and raises a hard error (exit code 3) on anything unexpected,
  including unreadable CONTRIBUTING files. A failed check can no longer
  degrade quietly into a GO verdict.

### Added
- Refusal-path tests: AI-policy ban detection on a sample CONTRIBUTING file,
  malformed targets (exit code 3 with a clean error, no traceback), and a
  positive test that the --me filter turns an own-comment-only thread into GO.
- CI smoke job: builds the wheel, installs it into a fresh venv, and
  exercises the installed `taken` entry point (--version, --help, and a
  malformed target expecting exit code 3).
- README documents the --json output schema (field names and verdict values).
- README with usage, three real examples, and the verdict rules;
  CONTRIBUTING guide; pull request template.
- GitHub Actions CI workflow (`.github/workflows/ci.yml`): runs `ruff check`,
  `ruff format --check`, and `pytest` on push and pull requests.
- Pytest suite (`tests/`): 31 tests covering the verdict logic (GO, TAKEN,
  CAUTION, and precedence) and the claimant-pattern matching.
- Command-line interface (`taken/cli.py`): `taken owner/repo#123` (full
  issue URLs also accepted), with `--json`, `--me`, `--version`, and
  `--help`. Exit codes 0 (GO), 1 (TAKEN), 2 (CAUTION), 3 (error).
- Verdict logic (`taken/verdict.py`): GO, TAKEN, and CAUTION, with TAKEN
  winning over CAUTION winning over GO. A claimant comment is a soft signal
  (CAUTION); a closed issue, an open linked PR, or an assignee is a hard
  signal (TAKEN).
- GitHub API checks (`taken/checks.py`): issue basics, timeline
  cross-reference scan, claimant language scan, AI policy detection, and repo
  health. All read-only via the `gh` CLI, using the invoker's own auth.
