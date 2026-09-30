# GraphQL fetch paths

`taken` ships three fetch paths for the single-issue pipeline. GraphQL is
the default for logged-in users (`gh` authenticated): one query per issue
instead of ~10 REST calls, with identical verdicts. REST remains the
default for anonymous use, the automatic per-issue fallback when the
GraphQL transport fails, and always available via `--rest` /
`TAKEN_REST=1`.

| Path | Flag / env | Transport |
| ---- | ---------- | --------- |
| A. REST (anonymous default, fallback, escape hatch) | `--rest` / `TAKEN_REST=1` | `gh api` subprocess per call, ~9-10 calls per issue |
| B. GraphQL subprocess (logged-in default) | `--graphql` / `TAKEN_GRAPHQL=1` | one `gh api graphql` query per issue (1 call typical, up to 4 with pagination) |
| C. GraphQL persistent session | `--persistent-session` / `TAKEN_PERSISTENT_SESSION=1` | same query over one HTTPS keep-alive connection held for the process lifetime |

## What the query fetches

One GraphQL query (`taken/graphql.py::ISSUE_QUERY`) returns every signal
the verdict needs:

- issue state, title, URL, creation date, author, labels, assignees
- comments (author, body, date, URL) with total count
- timeline `CrossReferencedEvent` / `ConnectedEvent` items, with linked PR
  number, title, state, merged flag, author, URL, and repo inline (no
  per-PR follow-up calls like the REST path needs)
- four aliased `object(expression: "HEAD:<path>")` lookups covering every
  CONTRIBUTING path the REST path probes sequentially
- `pushedAt`, recent merged-PR timestamps, and default-branch commit
  history (author login/email) for repo health

No REST fallback was needed: every REST signal has a GraphQL equivalent
(see "Fidelity notes" for the two normalizations). Cursor pagination
mirrors the REST scan ceilings (comments/timeline up to 500, commits up
to 300, merged-PR scan up to 2 pages), so deep repos never silently
truncate; extra round trips only happen when `pageInfo.hasNextPage` is
true.

## Fidelity notes

- **PR state enum.** GraphQL `PullRequest.state` has a distinct `MERGED`
  value; REST reports merged PRs as `state: "closed"` plus a merged flag.
  The parser normalizes `MERGED` to `"closed"` so the findings shape is
  byte-identical to REST.
- **Merged-PR scan.** REST lists closed PRs and counts those with
  `merged_at` in the window; GraphQL lists `states: MERGED` ordered by
  `UPDATED_AT` desc and paginates only while the oldest merge is still in
  the window (max 2 pages). Same count.
- **Missing objects.** `repository: null` or `issue: null` raises
  `NotFoundError`, matching the REST 404 behavior. A missing CONTRIBUTING
  path yields `null` for that alias and the scan moves to the next path,
  exactly like the REST 404-and-continue loop.
- **Fail-closed.** Any `errors` array in the GraphQL response raises
  instead of using partial `data`. `RATE_LIMITED` becomes the existing
  `RateLimitError` so the retry/backoff policy applies. Non-JSON and HTTP
  4xx/5xx responses on the persistent session raise `TakenError`.

## Security posture: path B vs path C

Path B keeps taken's existing auth model: the `gh` CLI holds the
credential and taken only ever sees API responses. taken never handles a
token.

Path C changes this deliberately, and only behind explicit opt-in:

- The token is obtained from `gh auth token` **once at startup**, via a
  subprocess, and held in a single in-memory variable on the session
  object.
- It is **never logged** (not even at debug level), **never written to
  disk** (the response cache stores only API payloads, never headers),
  and **never leaves the process** except as the `Authorization` header
  of requests to `api.github.com`.
- The session honors `HTTPS_PROXY`/`https_proxy` (CONNECT tunneling)
  like any well-behaved HTTP client; TLS is still negotiated end-to-end
  with `api.github.com` through the tunnel.
- A dropped keep-alive connection (proxy/server idle timeout) triggers
  one reconnect and one retry of the read-only query; a second failure
  surfaces as `TakenError`.

Why B remains the default: subprocess isolation means a compromised or
buggy taken process cannot leak a token it never held. Path C trades that
isolation for speed: one TLS handshake and no per-call process spawn. Use
C for batch runs on machines you trust; the token lives only as long as
the process.

## Measured results

Smoke run 2026-09-26: 5 real issues on `RogueAlg0/taken`
(#47, #100, #29, #118, #83), cache disabled, sequential with pauses.
All three paths produced **identical verdicts and identical findings**
on every issue.

| Issue | A: REST calls / wall | B: subprocess calls / wall | C: persistent calls / wall |
| ----- | -------------------- | -------------------------- | -------------------------- |
| #47  | 9 / 8.1s  | 3 / 3.5s | 3 / 2.4s |
| #100 | 10 / 9.2s | 3 / 3.4s | 4 / 2.2s |
| #29  | 9 / 9.1s  | 3 / 5.0s | 4 / 2.9s |
| #118 | 9 / 13.0s | 3 / 4.5s | 4 / 2.8s |
| #83  | 9 / 11.8s | 3 / 4.8s | 4 / 2.3s |

Averages: A ~9.2 calls / ~10.0s per issue; B 3 calls / ~4.2s;
C ~3.6 calls / ~2.5s.

Notes:

- B's 3 calls = 1 main query + 1 merged-PR page + 1 commit-history page
  (the taken repo is active enough to exceed the first page on both).
- C's occasional 4th call is a single reconnect+retry after the sandbox
  egress proxy closed an idle tunnel between issues; on a direct
  connection the tunnel stays up across the whole run.
- Rate-limit cost per issue: ~9-10 REST points (A) vs 1 GraphQL point
  per query (B/C), against the same 5000/hour budget.
- These numbers are from this environment and workload; your mileage
  will vary, but the ordering (C < B < A) follows directly from
  eliminating round trips and then eliminating subprocess spawns.

## MCP

`check_issue` accepts `graphql: bool = False` and
`persistent_session: bool = False`. Same opt-in semantics, same findings
shape.

## Known limitations

- `--discover` stays on the REST path: its candidate verifier runs in a
  thread pool and makes an extra `fetch_comments` call for maintainer
  engagement, so the single-query GraphQL shape does not cover it and the
  persistent session (one connection, not thread-safe) does not fit it.
- The web console (`docs/py/`) is unchanged; it runs the REST pipeline
  in-browser for the visitor rate budget.
