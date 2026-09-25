# taken?

`taken?` answers one question before you volunteer for a GitHub issue: is it
already taken?

The catch it was built for: GitHub shows "linked a pull request" events in the
issue timeline, but never in the comments. You can read every comment on an
issue and still miss that someone already opened a PR for it. `taken?` checks
the timeline, scans comments for people claiming the work, looks at assignees,
reads CONTRIBUTING.md for AI contribution policies, and checks whether the
repo is still active. Then it gives a verdict, GO, TAKEN, or CAUTION, with the
evidence cited.

## Requirements

- Python 3.10 or newer
- The [GitHub CLI](https://cli.github.com/) (`gh`), authenticated

`taken?` only makes read-only API calls through your own `gh` login. It never
sees or stores tokens, and it never writes anything to GitHub.

## Install as a tool

    uv tool install git+https://github.com/RogueAlg0/taken.git
    taken owner/repo#123

Or with pipx:

    pipx install git+https://github.com/RogueAlg0/taken.git
    taken owner/repo#123

## Usage

    uv run taken owner/repo#123

A full issue URL works too:

    uv run taken https://github.com/owner/repo/issues/123

Useful flags:

- `--json`: print the full findings as JSON instead of the human summary
- `--me LOGIN`: ignore your own comments when scanning for claimants
- `--version`, `--help`

Exit codes: 0 means GO, 1 means TAKEN, 2 means CAUTION, 3 means something
broke (bad target, no `gh`, API error).

## Examples

An issue with a PR already fixing it:

    $ uv run taken Exodus-Privacy/exodus#300
    taken? Exodus-Privacy/exodus#300
    verdict: TAKEN
      ...
      why:
        - open PR #700 already covers this: https://github.com/Exodus-Privacy/exodus/pull/700

An issue someone is already assigned to:

    $ uv run taken chahe-dridi/vscode-agent-bell#200
    taken? chahe-dridi/vscode-agent-bell#200
    verdict: TAKEN
      ...
      why:
        - assigned to: dk5488

A clean issue, with your own volunteering comment filtered out:

    $ uv run taken snowflakedb/snowflake-cli#3145 --me RogueAlg0
    taken? snowflakedb/snowflake-cli#3145
    verdict: GO
      ...
      why:
        - no linked PRs, no assignees, no claimants, repo is active

## How the verdict works

TAKEN wins over CAUTION, which wins over GO.

- TAKEN: the issue is closed, an open PR links to it, or someone is assigned.
- CAUTION: a comment says someone wants it (soft claim, they may have moved
  on), a linked PR was merged but the issue is still open, the repo has an AI
  policy to respect, or the repo looks inactive.
- GO: none of the above.

The claimant scan is a heuristic over comment text, not proof. The verdict
always prints its evidence so you can judge for yourself.

## Development

    uv sync          # install dev tools (ruff, pytest)
    uv run pytest
    uv run ruff check
    uv run ruff format --check

## AI assistance

This project is built with AI assistance, and says so openly. Every
contribution is reviewed and understood by its author before it lands.
