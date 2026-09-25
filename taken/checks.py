"""Read-only GitHub checks used by taken.

All access goes through the `gh` CLI, so the tool uses the invoker's own
authentication and never sees, stores, or handles any token.
"""

import base64
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone

API_TIMEOUT = 60
HEALTH_WINDOW_DAYS = 30

PR_URL_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)")

CLAIMANT_PATTERNS = [
    "assign me",
    "can i work on",
    "i'd like to take",
    "i would like to work",
    "please assign",
    "working on this",
    "i'll take this",
    "i will take",
    "i'd love to take",
    "i'd love to work on",
    "could you assign",
    "assign this issue to me",
]

BAN_PHRASES = [
    "does not accept ai",
    "do not accept ai",
    "will not accept ai",
    "no ai-generated",
]

DISCLOSURE_PHRASES = [
    "assisted-by",
    "ai-assisted",
    "disclose",
    "generative ai",
]

CONTRIBUTING_PATHS = [
    "CONTRIBUTING.md",
    ".github/CONTRIBUTING.md",
    "docs/CONTRIBUTING.md",
    "CONTRIBUTING.rst",
]


class TakenError(Exception):
    """Something went wrong talking to GitHub."""


class NotFoundError(TakenError):
    """A GitHub resource did not exist (HTTP 404)."""


def gh_api(endpoint, params=None):
    """GET a GitHub API endpoint via `gh api` and return parsed JSON."""
    cmd = ["gh", "api", endpoint.lstrip("/")]
    for key, value in (params or {}).items():
        cmd.extend(["-f", f"{key}={value}"])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=API_TIMEOUT)
    except FileNotFoundError:
        raise TakenError("the `gh` CLI is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise TakenError(f"`gh api {endpoint}` timed out after {API_TIMEOUT}s")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "404" in err or "Not Found" in err:
            raise NotFoundError(endpoint)
        raise TakenError(f"`gh api {endpoint}` failed: {err[:300]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise TakenError(f"`gh api {endpoint}` did not return JSON")


def check_issue(owner, repo, number):
    """Fetch the basic facts about an issue."""
    data = gh_api(f"repos/{owner}/{repo}/issues/{number}")
    return {
        "number": number,
        "state": data.get("state"),
        "title": data.get("title"),
        "labels": [label["name"] for label in data.get("labels", [])],
        "assignees": [user["login"] for user in data.get("assignees", [])],
        "comment_count": data.get("comments", 0),
        "author": (data.get("user") or {}).get("login"),
        "url": data.get("html_url"),
        "created_at": data.get("created_at"),
    }


def check_timeline(owner, repo, number):
    """Find PRs linked to the issue via timeline cross-reference events.

    These events never appear in the issue comments, which is the main
    reason this tool exists.
    """
    events = gh_api(f"repos/{owner}/{repo}/issues/{number}/timeline", {"per_page": "100"})
    linked = []
    seen = set()
    for event in events or []:
        if event.get("event") not in ("cross-referenced", "connected"):
            continue
        src = (event.get("source") or {}).get("issue") or {}
        match = PR_URL_RE.match(src.get("html_url") or "")
        if not match:
            continue
        pr_owner, pr_repo, pr_number = match.groups()
        key = (pr_owner, pr_repo, pr_number)
        if key in seen:
            continue
        seen.add(key)
        pr = gh_api(f"repos/{pr_owner}/{pr_repo}/pulls/{pr_number}")
        linked.append(
            {
                "number": int(pr_number),
                "title": pr.get("title"),
                "state": pr.get("state"),
                "merged": bool(pr.get("merged_at")),
                "author": (pr.get("user") or {}).get("login"),
                "url": pr.get("html_url"),
            }
        )
    return linked


def find_claimant_hits(comments, me=None):
    """Scan comment bodies for claimant language, skipping the given login."""
    hits = []
    me_lower = (me or "").lower()
    for comment in comments or []:
        author = (comment.get("user") or {}).get("login", "")
        if me_lower and author.lower() == me_lower:
            continue
        body = comment.get("body") or ""
        lowered = body.lower()
        matched = next((p for p in CLAIMANT_PATTERNS if p in lowered), None)
        if matched is None:
            continue
        snippet = " ".join(body.split())
        hits.append(
            {
                "author": author,
                "date": (comment.get("created_at") or "")[:10],
                "pattern": matched,
                "snippet": snippet[:160],
                "url": comment.get("html_url"),
            }
        )
    return hits


def check_claimants(owner, repo, number, me=None):
    """Fetch issue comments and scan them for claimant language."""
    comments = gh_api(f"repos/{owner}/{repo}/issues/{number}/comments", {"per_page": "100"})
    return find_claimant_hits(comments, me=me)


def _first_line_with(text, phrase):
    for line in text.splitlines():
        if phrase in line.lower():
            return line.strip()[:160]
    return ""


def classify_policy(text):
    """Classify a CONTRIBUTING-style document: ban, disclosure-required, or none-found."""
    for phrase in BAN_PHRASES:
        snippet = _first_line_with(text, phrase)
        if snippet:
            return "ban", snippet
    for phrase in DISCLOSURE_PHRASES:
        snippet = _first_line_with(text, phrase)
        if snippet:
            return "disclosure-required", snippet
    return "none-found", ""


def check_ai_policy(owner, repo):
    """Look for an AI contribution policy in CONTRIBUTING files."""
    for path in CONTRIBUTING_PATHS:
        try:
            data = gh_api(f"repos/{owner}/{repo}/contents/{path}")
        except NotFoundError:
            continue
        if not isinstance(data, dict):
            continue
        try:
            text = base64.b64decode(data.get("content") or "").decode("utf-8", errors="replace")
        except Exception:
            continue
        verdict, snippet = classify_policy(text)
        return {"verdict": verdict, "snippet": snippet, "source": path}
    return {"verdict": "none-found", "snippet": "", "source": None}


def check_repo_health(owner, repo, window_days=HEALTH_WINDOW_DAYS):
    """Check recent pushes and merged PRs as a rough activity signal."""
    data = gh_api(f"repos/{owner}/{repo}")
    pushed_at = data.get("pushed_at") or ""
    pushed_recently = False
    if pushed_at:
        pushed_dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
        pushed_recently = datetime.now(timezone.utc) - pushed_dt <= timedelta(days=window_days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    recent_merges = 0
    for page in (1, 2):
        prs = gh_api(
            f"repos/{owner}/{repo}/pulls",
            {
                "state": "closed",
                "per_page": "50",
                "page": str(page),
                "sort": "updated",
                "direction": "desc",
            },
        )
        if not prs:
            break
        for pr in prs:
            merged_at = pr.get("merged_at")
            if not merged_at:
                continue
            merged_dt = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
            if merged_dt >= cutoff:
                recent_merges += 1
        if len(prs) < 50:
            break
    return {
        "pushed_at": pushed_at[:10],
        "pushed_recently": pushed_recently,
        "recent_merges": recent_merges,
        "stars": data.get("stargazers_count", 0),
    }
