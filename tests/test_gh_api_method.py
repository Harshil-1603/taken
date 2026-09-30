"""Regression tests: REST gh_api() must pin --method GET (issue #201).

Stock `gh` switches `gh api` from GET to POST whenever -f/-F parameters are
added. Without an explicit --method, every parameterized REST call taken
makes would go out as POST under a real gh CLI (POST /search/issues has no
route; POST /repos/{o}/{r}/issues reads as "create an issue").

The GraphQL path intentionally keeps the auto-POST (the GraphQL endpoint
only accepts POST), so the pin belongs to the REST gh_api() only.
"""

import json

from taken import checks, graphql


class _Proc:
    returncode = 0
    stdout = json.dumps({"data": {}})
    stderr = ""


def _capture(module, monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return _Proc()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(checks, "_CACHE_ENABLED", False)
    return seen


def test_gh_api_pins_get_with_params(monkeypatch):
    seen = _capture(checks, monkeypatch)
    checks.gh_api("repos/octo/repo/issues", {"state": "open", "per_page": "100"})
    cmd = seen["cmd"]
    assert cmd[:4] == ["gh", "api", "--method", "GET"]
    assert "repos/octo/repo/issues" in cmd
    assert "-f" in cmd


def test_gh_api_pins_get_without_params(monkeypatch):
    seen = _capture(checks, monkeypatch)
    checks.gh_api("repos/octo/repo/issues/1")
    assert seen["cmd"][:4] == ["gh", "api", "--method", "GET"]
    assert "-f" not in seen["cmd"]


def test_graphql_path_keeps_auto_post(monkeypatch):
    """The GraphQL endpoint only takes POST: no --method pin there."""
    seen = _capture(graphql, monkeypatch)
    graphql.graphql_via_gh("query { viewer { login } }", {"x": "y"})
    cmd = seen["cmd"]
    assert cmd[:3] == ["gh", "api", "graphql"]
    assert "--method" not in cmd
