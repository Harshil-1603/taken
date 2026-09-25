"""Tests for claimant-pattern matching and AI policy classification."""

import pytest

from taken.checks import CLAIMANT_PATTERNS, classify_policy, find_claimant_hits


def make_comment(body, author="someone"):
    return {
        "user": {"login": author},
        "body": body,
        "created_at": "2026-09-20T10:00:00Z",
        "html_url": "https://github.com/octo/repo/issues/1#issuecomment-1",
    }


@pytest.mark.parametrize("phrase", CLAIMANT_PATTERNS)
def test_each_pattern_matches(phrase):
    hits = find_claimant_hits([make_comment(f"Hi! {phrase}, thanks!")])
    assert len(hits) == 1
    assert hits[0]["author"] == "someone"
    assert hits[0]["pattern"] == phrase


def test_matching_is_case_insensitive():
    hits = find_claimant_hits([make_comment("I'd LIKE to TAKE this one on, please.")])
    assert len(hits) == 1
    assert hits[0]["pattern"] == "i'd like to take"


def test_plain_comment_is_not_a_claim():
    hits = find_claimant_hits(
        [make_comment("I reproduced this locally; the trace points at the parser.")]
    )
    assert hits == []


def test_me_filtering_skips_own_comments():
    comments = [make_comment("Hi, I'd like to take this one on.", author="RogueAlg0")]
    assert find_claimant_hits(comments, me="RogueAlg0") == []
    assert find_claimant_hits(comments, me="roguealg0") == []
    assert len(find_claimant_hits(comments)) == 1


def test_snippet_is_trimmed_and_single_line():
    hits = find_claimant_hits([make_comment("please assign\nthis to me " + "x" * 300)])
    assert "\n" not in hits[0]["snippet"]
    assert len(hits[0]["snippet"]) <= 160


def test_policy_ban_detected():
    verdict, snippet = classify_policy("This project does not accept AI-generated contributions.")
    assert verdict == "ban"
    assert snippet != ""


def test_policy_disclosure_detected():
    verdict, _ = classify_policy("Please disclose AI assistance with an Assisted-by trailer.")
    assert verdict == "disclosure-required"


def test_policy_none_found():
    verdict, snippet = classify_policy("Be kind. Write tests. Update the docs.")
    assert verdict == "none-found"
    assert snippet == ""


def test_policy_ban_wins_over_disclosure():
    text = "We do not accept AI-generated contributions per the Generative AI Policy."
    verdict, _ = classify_policy(text)
    assert verdict == "ban"
