# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
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
