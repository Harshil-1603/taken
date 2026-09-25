# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- Verdict logic (`taken/verdict.py`): GO, TAKEN, and CAUTION, with TAKEN
  winning over CAUTION winning over GO. A claimant comment is a soft signal
  (CAUTION); a closed issue, an open linked PR, or an assignee is a hard
  signal (TAKEN).
- GitHub API checks (`taken/checks.py`): issue basics, timeline
  cross-reference scan, claimant language scan, AI policy detection, and repo
  health. All read-only via the `gh` CLI, using the invoker's own auth.
