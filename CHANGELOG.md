# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Fixed
- Fail closed: every GitHub API check now validates the shape of the
  response and raises a hard error (exit code 3) on anything unexpected,
  including unreadable CONTRIBUTING files. A failed check can no longer
  degrade quietly into a GO verdict.

### Added
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
