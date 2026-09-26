"""Candidate discovery for taken.

Piggybacks on GitHub's issue search API, the same source the web aggregators
use, for raw candidates. What the aggregators don't do (and we do): run
taken's full verification on every candidate and rank by maintainer
responsiveness, the signal that best predicts whether volunteering will go
anywhere. No aggregator filters on that.
"""

from datetime import datetime, timedelta, timezone

from . import checks
from .verdict import GO, decide

SEARCH_LABELS = ["good first issue", "good-first-issue", "beginner friendly", "help wanted"]
SEARCH_PER_PAGE = 50
VERIFY_POOL = 40


def build_query(label, language=None, updated_after=None):
    parts = ["is:open", "is:issue", "no:assignee", f'label:"{label}"']
    if updated_after:
        parts.append(f"updated:>={updated_after}")
    if language:
        parts.append(f"language:{language}")
    return " ".join(parts)


def repo_of(search_item):
    """Extract (owner, repo) from a search result's repository_url."""
    url = (search_item.get("repository_url") or "").rstrip("/").split("/")
    if len(url) < 2:
        return None
    return url[-2], url[-1]


def maintainer_engaged(issue, comments):
    """Heuristic: someone other than the author, not a bot, commented.

    A maintainer reply is the strongest cheap signal that volunteering on
    the issue will get a response. Bots don't count.
    """
    author = issue.get("author")
    for comment in comments:
        login = (comment.get("user") or {}).get("login") or ""
        if login and login != author and not login.endswith("[bot]"):
            return True
    return False


def _days_ago(iso_ts):
    try:
        dt = datetime.fromisoformat((iso_ts or "").replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


def score_candidate(findings, updated_at, engaged):
    """Explainable score. Returns (points, [reasons])."""
    points = 0
    why = []
    if engaged:
        points += 3
        why.append("maintainer replied")
    age_days = _days_ago(updated_at)
    if age_days is not None and age_days <= 7:
        points += 2
        why.append(f"updated {age_days}d ago")
    push_days = _days_ago(findings["repo_health"].get("pushed_at") or "")
    if push_days is not None and push_days <= 7:
        points += 1
        why.append(f"repo pushed {push_days}d ago")
    if not why:
        why.append("passed verification")
    return points, why


def discover(limit=10, language=None, label=None, min_stars=0, me=None):
    """Search, verify, and rank contribution candidates.

    Returns a list of dicts sorted by score (desc), then recency (desc):
    target, score, why, verdict, reasons, findings, updated_at.
    """
    updated_after = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    labels = [label] if label else SEARCH_LABELS
    candidates = []
    seen = set()
    for lab in labels:
        query = build_query(lab, language=language, updated_after=updated_after)
        for item in checks.search_issues(query, per_page=SEARCH_PER_PAGE):
            where = repo_of(item)
            if not where:
                continue
            owner, repo = where
            key = (owner, repo, item.get("number"))
            if key in seen:
                continue
            seen.add(key)
            candidates.append((owner, repo, item.get("number"), item))
            if len(candidates) >= VERIFY_POOL:
                break
        if len(candidates) >= VERIFY_POOL:
            break

    ranked = []
    for owner, repo, number, item in candidates:
        try:
            findings = checks.run_checks(owner, repo, number, me=me)
        except checks.TakenError:
            continue  # fail-closed per issue; keep scanning the rest
        verdict, reasons = decide(findings)
        if verdict != GO:
            continue
        if (findings["repo_health"].get("stars") or 0) < min_stars:
            continue
        comments = checks.fetch_comments(owner, repo, number)
        engaged = maintainer_engaged(findings["issue"], comments)
        points, why = score_candidate(findings, item.get("updated_at"), engaged)
        ranked.append(
            {
                "target": f"{owner}/{repo}#{number}",
                "score": points,
                "why": why,
                "verdict": verdict,
                "reasons": reasons,
                "findings": findings,
                "updated_at": item.get("updated_at") or "",
            }
        )
    ranked.sort(key=lambda r: r["updated_at"])
    ranked.sort(key=lambda r: -r["score"])
    return ranked[:limit]


def _comments(owner, repo, number):
    endpoint = f"repos/{owner}/{repo}/issues/{number}/comments"
    return checks._require_list(checks.gh_api(endpoint, {"per_page": "100"}), endpoint)
