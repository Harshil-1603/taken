"""Refusal-path tests: the tool must say no (or fail loudly) in the right way."""

import base64

from test_verdict import base_findings

from taken import checks
from taken.cli import main
from taken.verdict import GO, decide


def contributing_api(text):
    def fake(endpoint, params=None):
        if endpoint.endswith("CONTRIBUTING.md"):
            return {
                "content": base64.b64encode(text.encode()).decode(),
                "encoding": "base64",
            }
        raise checks.NotFoundError(endpoint)

    return fake


def test_ban_language_detected():
    text = "## Contributing\n\nWe do not accept AI-generated contributions to this repo.\n"
    verdict, snippet = checks.classify_policy(text)
    assert verdict == "ban"
    assert "do not accept ai" in snippet.lower()


def test_check_ai_policy_reports_ban(monkeypatch):
    text = "Please read this first: we do not accept AI-generated contributions.\n"
    monkeypatch.setattr(checks, "gh_api", contributing_api(text))
    result = checks.check_ai_policy("octo", "repo")
    assert result["verdict"] == "ban"
    assert result["source"] == "CONTRIBUTING.md"
    assert result["snippet"]


def test_disclosure_language_detected():
    text = "All AI-assisted changes must include an Assisted-by trailer.\n"
    verdict, _ = checks.classify_policy(text)
    assert verdict == "disclosure-required"


def test_malformed_target_is_exit_3(capsys):
    assert main(["not-a-target"]) == 3
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "could not parse" in err


def test_target_without_issue_number_is_exit_3(capsys):
    assert main(["octo/repo"]) == 3
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "could not parse" in err


def test_empty_target_is_exit_3():
    assert main([""]) == 3


def make_comment(author, body):
    return {
        "user": {"login": author},
        "body": body,
        "created_at": "2026-09-25T10:00:00Z",
        "html_url": "https://github.com/octo/repo/issues/1#issuecomment-1",
    }


def test_me_filter_turns_claimant_thread_into_go(monkeypatch):
    comments = [
        make_comment("RogueAlg0", "I'd like to take this one on, please."),
        make_comment("bystander", "Same problem here, any workaround?"),
    ]
    monkeypatch.setattr(checks, "gh_api", lambda endpoint, params=None: comments)

    hits = checks.check_claimants("octo", "repo", 1, me="RogueAlg0")
    assert hits == []

    findings = base_findings()
    findings["claimants"] = hits
    verdict, _ = decide(findings)
    assert verdict == GO


def test_same_thread_without_me_filter_is_not_go(monkeypatch):
    comments = [make_comment("RogueAlg0", "I'd like to take this one on, please.")]
    monkeypatch.setattr(checks, "gh_api", lambda endpoint, params=None: comments)

    hits = checks.check_claimants("octo", "repo", 1)
    assert len(hits) == 1

    findings = base_findings()
    findings["claimants"] = hits
    verdict, _ = decide(findings)
    assert verdict != GO
