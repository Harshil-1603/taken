"""Tests for the opt-in GraphQL fetch paths (taken/graphql.py)."""

import argparse
import http.client
import json
import os
import subprocess
from unittest import mock

import pytest

from taken import checks, graphql
from taken.verdict import decide


def _issue_node(**over):
    node = {
        "state": "OPEN",
        "title": "Some issue",
        "url": "https://github.com/o/r/issues/1",
        "createdAt": "2026-09-01T00:00:00Z",
        "author": {"login": "someone"},
        "assignees": {"nodes": [{"login": "dev"}]},
        "labels": {"nodes": [{"name": "good first issue"}]},
        "comments": {
            "totalCount": 1,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {
                    "author": {"login": "volunteer"},
                    "body": "I would like to work on this",
                    "createdAt": "2026-09-02T00:00:00Z",
                    "url": "https://github.com/o/r/issues/1#c1",
                }
            ],
        },
        "timelineItems": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {
                    "__typename": "CrossReferencedEvent",
                    "source": {
                        "__typename": "PullRequest",
                        "number": 7,
                        "title": "Fix thing",
                        "state": "OPEN",
                        "mergedAt": None,
                        "url": "https://github.com/o/r/pull/7",
                        "author": {"login": "dev"},
                        "repository": {"nameWithOwner": "o/r"},
                    },
                }
            ],
        },
    }
    node.update(over)
    return node


def _repo_node(**over):
    repo = {
        "pushedAt": "2026-09-26T00:00:00Z",
        "issue": _issue_node(),
        "ai1": {"text": "This project does not accept AI-generated contributions."},
        "ai2": None,
        "ai3": None,
        "ai4": None,
        "mergedPRs": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"mergedAt": "2026-09-20T00:00:00Z"}],
        },
        "defaultBranchRef": {
            "target": {
                "history": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {"author": {"user": {"login": "dev"}, "email": "d@x"}},
                        {"author": {"user": {"login": "dev"}, "email": "d@x"}},
                        {"author": {"user": None, "email": "anon@x"}},
                    ],
                }
            }
        },
    }
    repo.update(over)
    return repo


def _payload(repo=None):
    return {
        "data": {
            "repository": _repo_node() if repo is None else repo,
            "rateLimit": {"limit": 5000, "cost": 1},
        }
    }


# --- mode resolution -------------------------------------------------------


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    """GraphQL transport tests must not touch the real cache."""
    monkeypatch.setattr(checks, "_CACHE_ENABLED", False)


def _args(**kw):
    args = argparse.Namespace(graphql=False, persistent_session=False)
    for k, v in kw.items():
        setattr(args, k, v)
    return args


def test_fetch_mode_defaults_to_rest():
    assert graphql.fetch_mode(_args()) == "rest"
    assert graphql.fetch_mode(None) == "rest"


def test_fetch_mode_flag_and_env():
    assert graphql.fetch_mode(_args(graphql=True)) == "graphql"
    with mock.patch.dict(os.environ, {"TAKEN_GRAPHQL": "1"}):
        assert graphql.fetch_mode(_args()) == "graphql"


def test_fetch_mode_persistent_wins():
    assert graphql.fetch_mode(_args(persistent_session=True)) == "persistent"
    with mock.patch.dict(os.environ, {"TAKEN_PERSISTENT_SESSION": "1"}):
        assert graphql.fetch_mode(_args(graphql=True)) == "persistent"


# --- error handling --------------------------------------------------------


def test_raise_for_errors_none():
    graphql._raise_for_errors({"data": {}}, "x")  # no raise


def test_raise_for_errors_names_type():
    with pytest.raises(checks.TakenError, match="NOT_FOUND"):
        graphql._raise_for_errors(
            {"errors": [{"type": "NOT_FOUND", "message": "nope"}], "data": None}, "x"
        )


def test_raise_for_errors_partial_data_fails_closed():
    with pytest.raises(checks.TakenError):
        graphql._raise_for_errors(
            {"errors": [{"type": "FORBIDDEN", "message": "x"}], "data": {"repository": {}}},
            "x",
        )


def test_raise_for_errors_rate_limited():
    with pytest.raises(checks.RateLimitError):
        graphql._raise_for_errors(
            {"errors": [{"type": "RATE_LIMITED", "message": "slow down"}], "data": None},
            "x",
        )


# --- subprocess transport (path B) -----------------------------------------


def _run_result(stdout, rc=0, stderr=""):
    proc = mock.Mock()
    proc.stdout = stdout
    proc.returncode = rc
    proc.stderr = stderr
    return proc


def test_graphql_via_gh_posts_query():
    payload = _payload()
    with mock.patch.object(subprocess, "run", return_value=_run_result(json.dumps(payload))) as run:
        out = graphql.graphql_via_gh("query Q { x }", {"number": 1})
    assert out == payload
    cmd = run.call_args[0][0]
    assert cmd[:3] == ["gh", "api", "graphql"]
    assert "-f" in cmd and any(a.startswith("query=") for a in cmd)


def test_graphql_via_gh_errors_fail_closed():
    bad = {"errors": [{"type": "NOT_FOUND", "message": "x"}]}
    with mock.patch.object(subprocess, "run", return_value=_run_result(json.dumps(bad))):
        with pytest.raises(checks.TakenError):
            graphql.graphql_via_gh("query Q { x }", {})


def test_graphql_via_gh_rate_limited_retries_then_raises():
    bad = {"errors": [{"type": "RATE_LIMITED", "message": "slow"}]}
    with mock.patch.object(subprocess, "run", return_value=_run_result(json.dumps(bad))):
        with mock.patch("time.sleep"):
            with pytest.raises(checks.RateLimitError):
                graphql.graphql_via_gh("query Q { x }", {})


def test_graphql_via_gh_missing_binary():
    with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError):
        with pytest.raises(checks.TakenError, match="`gh` CLI"):
            graphql.graphql_via_gh("query Q { x }", {})


# --- persistent session (path C) --------------------------------------------


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body


class _FakeConn:
    instances = []

    def __init__(self, *args, **kwargs):
        self.requests = []
        self.response = _FakeResponse(200, json.dumps(_payload()).encode())
        _FakeConn.instances.append(self)

    def request(self, method, url, body=None, headers=None):
        self.requests.append((method, url, body, headers))

    def set_tunnel(self, host, port=None, headers=None):
        self.tunnel = (host, port)

    def getresponse(self):
        return self.response

    def close(self):
        pass


def test_persistent_session_reuses_connection_and_token():
    _FakeConn.instances.clear()
    provider = mock.Mock(return_value="sekret-token")
    with mock.patch.object(http.client, "HTTPSConnection", _FakeConn):
        sess = graphql.PersistentGraphQLSession(token_provider=provider)
        out1 = sess.query("query Q { x }", {"a": 1})
        out2 = sess.query("query Q { x }", {"a": 1})
    assert out1["data"]["repository"]["pushedAt"]
    assert out2["data"]["repository"]["pushedAt"]
    assert provider.call_count == 1  # token fetched once, held in memory
    assert len(_FakeConn.instances) == 1  # one keep-alive connection
    assert len(_FakeConn.instances[0].requests) == 2
    _method, _url, _body, headers = _FakeConn.instances[0].requests[0]
    assert headers["Authorization"] == "Bearer sekret-token"


def test_persistent_session_never_logs_token(capfd):
    _FakeConn.instances.clear()
    with mock.patch.object(http.client, "HTTPSConnection", _FakeConn):
        sess = graphql.PersistentGraphQLSession(token_provider=lambda: "sekret-token")
        sess.query("query Q { x }", {})
    out, err = capfd.readouterr()
    assert "sekret-token" not in out and "sekret-token" not in err


def test_persistent_session_auth_token_failure():
    with mock.patch.object(graphql, "_gh_auth_token", side_effect=checks.TakenError("no gh")):
        sess = graphql.PersistentGraphQLSession()
        with pytest.raises(checks.TakenError):
            sess.query("query Q { x }", {})


def test_persistent_session_429_becomes_rate_limit_error():
    _FakeConn.instances.clear()

    class _Conn429(_FakeConn):
        def getresponse(self):
            return _FakeResponse(429, b"{}")

    with mock.patch.object(http.client, "HTTPSConnection", _Conn429):
        sess = graphql.PersistentGraphQLSession(token_provider=lambda: "t")
        with pytest.raises(checks.RateLimitError):
            sess.query("query Q { x }", {})


def test_persistent_session_dropped_connection_retries_once():
    """A proxy-closed idle connection reconnects and the query is retried."""
    _FakeConn.instances.clear()

    class _ConnFlaky(_FakeConn):
        calls = 0

        def getresponse(self):
            type(self).calls += 1
            if type(self).calls == 1:
                raise http.client.RemoteDisconnected("boom")
            return self.response

    with mock.patch.object(http.client, "HTTPSConnection", _ConnFlaky):
        sess = graphql.PersistentGraphQLSession(token_provider=lambda: "t")
        out = sess.query("query Q { x }", {})
        assert out["data"]["repository"]["pushedAt"]
        assert sess.calls == 2  # failed attempt + one retry


def test_persistent_session_persistent_failure_raises_taken_error():
    _FakeConn.instances.clear()

    class _ConnDead(_FakeConn):
        def getresponse(self):
            raise http.client.RemoteDisconnected("always")

    with mock.patch.object(http.client, "HTTPSConnection", _ConnDead):
        sess = graphql.PersistentGraphQLSession(token_provider=lambda: "t")
        with pytest.raises(checks.TakenError, match="connection failed"):
            sess.query("query Q { x }", {})


# --- findings mapping -------------------------------------------------------


def _run_with_fake_transport(monkeypatch, payload, mode="graphql"):
    if mode == "persistent":
        fake = mock.Mock()
        fake.query.return_value = payload
        monkeypatch.setattr(graphql, "get_session", lambda: fake)
    else:
        monkeypatch.setattr(graphql, "graphql_via_gh", lambda q, v: payload)
    return graphql.run_checks_graphql("o", "r", 1, mode=mode)


def test_findings_shape_matches_rest_contract(monkeypatch):
    findings = _run_with_fake_transport(monkeypatch, _payload())
    assert set(findings) == {
        "target",
        "issue",
        "linked_prs",
        "claimants",
        "ai_policy",
        "repo_health",
    }
    assert set(findings["issue"]) == {
        "number",
        "state",
        "title",
        "labels",
        "assignees",
        "comment_count",
        "author",
        "url",
        "created_at",
    }
    assert findings["issue"]["state"] == "open"
    assert findings["issue"]["assignees"] == ["dev"]
    assert findings["issue"]["labels"] == ["good first issue"]
    assert findings["linked_prs"][0]["number"] == 7
    assert findings["linked_prs"][0]["state"] == "open"
    assert findings["claimants"][0]["author"] == "volunteer"
    assert findings["ai_policy"]["verdict"] == "ban"
    assert findings["ai_policy"]["source"] == "CONTRIBUTING.md"
    assert findings["repo_health"]["recent_merges"] == 1
    assert findings["repo_health"]["contributors"] == 2  # dev + anon@x


def test_verdict_matches_rest_findings(monkeypatch):
    findings = _run_with_fake_transport(monkeypatch, _payload())
    rest_findings = {
        "target": "o/r#1",
        "issue": {
            "number": 1,
            "state": "open",
            "title": "Some issue",
            "labels": ["good first issue"],
            "assignees": ["dev"],
            "comment_count": 1,
            "author": "someone",
            "url": "https://github.com/o/r/issues/1",
            "created_at": "2026-09-01T00:00:00Z",
        },
        "linked_prs": [
            {
                "number": 7,
                "title": "Fix thing",
                "state": "open",
                "merged": False,
                "author": "dev",
                "url": "https://github.com/o/r/pull/7",
            }
        ],
        "claimants": [
            {
                "author": "volunteer",
                "date": "2026-09-02",
                "url": "u",
                "pattern": "work on",
                "snippet": "I would like to work on this",
            }
        ],
        "ai_policy": {"verdict": "ban", "snippet": "", "source": "CONTRIBUTING.md"},
        "repo_health": {
            "pushed_at": "2026-09-26",
            "pushed_recently": True,
            "recent_merges": 1,
            "contributors": 2,
            "contributors_window_days": 90,
        },
    }
    assert decide(findings) == decide(rest_findings)


def test_merged_pr_state_normalizes_to_closed(monkeypatch):
    """GraphQL MERGED enum maps to REST's state "closed" + merged flag."""
    repo = _repo_node()
    src = repo["issue"]["timelineItems"]["nodes"][0]["source"]
    src["state"] = "MERGED"
    src["mergedAt"] = "2026-09-10T00:00:00Z"
    findings = _run_with_fake_transport(monkeypatch, _payload(repo))
    pr = findings["linked_prs"][0]
    assert pr["state"] == "closed"
    assert pr["merged"] is True


def test_missing_issue_raises_not_found(monkeypatch):
    repo = _repo_node(issue=None)
    with pytest.raises(checks.NotFoundError):
        _run_with_fake_transport(monkeypatch, _payload(repo))


def test_missing_repo_raises_not_found(monkeypatch):
    with pytest.raises(checks.NotFoundError):
        _run_with_fake_transport(monkeypatch, {"data": {"repository": None}})


def test_persistent_mode_routes_to_session(monkeypatch):
    findings = _run_with_fake_transport(monkeypatch, _payload(), mode="persistent")
    assert findings["issue"]["number"] == 1


def test_comment_pagination_follows_cursor(monkeypatch):
    page1 = _repo_node()
    page1["issue"]["comments"] = {
        "totalCount": 2,
        "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
        "nodes": page1["issue"]["comments"]["nodes"],
    }
    page2 = _repo_node()
    page2["issue"]["comments"] = {
        "totalCount": 2,
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [
            {
                "author": {"login": "second"},
                "body": "I'll take this on",
                "createdAt": "2026-09-03T00:00:00Z",
                "url": "https://github.com/o/r/issues/1#c2",
            }
        ],
    }
    calls = []

    def fake_fetch(q, v):
        calls.append(v.get("commentsAfter"))
        return {"data": {"repository": page2 if v.get("commentsAfter") else page1}}

    monkeypatch.setattr(graphql, "graphql_via_gh", fake_fetch)
    findings = graphql.run_checks_graphql("o", "r", 1, mode="graphql")
    assert calls == [None, "c1"]
    assert {c["author"] for c in findings["claimants"]} == {"volunteer", "second"}


# --- CLI / MCP plumbing -----------------------------------------------------


def _minimal_findings():
    return {
        "target": "o/r#1",
        "issue": {
            "number": 1,
            "state": "open",
            "title": "t",
            "labels": [],
            "assignees": [],
            "comment_count": 0,
            "author": None,
            "url": "u",
            "created_at": "2026-09-01T00:00:00Z",
        },
        "linked_prs": [],
        "claimants": [],
        "ai_policy": {"verdict": "none-found", "snippet": "", "source": None},
        "repo_health": {
            "pushed_at": "2026-09-26",
            "pushed_recently": True,
            "recent_merges": 1,
            "contributors": 1,
            "contributors_window_days": 90,
        },
    }


def test_cli_check_one_routes_modes(monkeypatch):
    from taken import cli

    with mock.patch.object(graphql, "run_checks_graphql") as rg:
        rg.return_value = _minimal_findings()
        cli.check_one("o", "r", 1, None, mode="graphql")
        assert rg.call_count == 1
    with mock.patch.object(checks, "run_checks") as rc:
        rc.return_value = _minimal_findings()
        cli.check_one("o", "r", 1, None, mode="rest")
        assert rc.call_count == 1


def test_mcp_check_issue_graphql_param(monkeypatch):
    from taken import mcp_server

    with mock.patch.object(graphql, "run_checks_graphql") as rg:
        rg.return_value = _minimal_findings()
        payload = mcp_server.check_issue("o", "r", 1, graphql=True)
        assert rg.call_count == 1
        assert payload["target"] == "o/r#1"


def test_mcp_check_issue_persistent_param(monkeypatch):
    from taken import mcp_server

    with mock.patch.object(graphql, "run_checks_graphql") as rg:
        rg.return_value = _minimal_findings()
        mcp_server.check_issue("o", "r", 1, persistent_session=True)
        _, kwargs = rg.call_args
        assert kwargs["mode"] == "persistent"
