"""Read-only GitHub checks used by taken.

All access goes through the `gh` CLI, so the tool uses the invoker's own
authentication and never sees, stores, or handles any token.
"""

import base64
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

API_TIMEOUT = 60
HEALTH_WINDOW_DAYS = 30
CACHE_TTL_SECONDS = 3600

# Set to False (via --no-cache) to bypass the response cache.
_CACHE_ENABLED = True

# Guards the cache file: discover mode verifies candidates in threads, and a
# read-modify-write on one JSON file is not atomic.
_CACHE_LOCK = threading.Lock()

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


def _require_dict(value, endpoint):
    """Fail closed: a check that got a non-object response must error, not guess."""
    if not isinstance(value, dict):
        raise TakenError(f"`gh api {endpoint}` returned an unexpected response")
    return value


def search_issues(query, per_page=50):
    """Search issues via the GitHub search API.

    This is the same source the web aggregators use; we piggyback on it for
    candidates and do our own verification and ranking on top.
    """
    data = _require_dict(
        gh_api(
            "search/issues",
            {"q": query, "per_page": str(per_page), "sort": "updated", "order": "desc"},
        ),
        "search/issues",
    )
    items = data.get("items")
    if not isinstance(items, list):
        raise TakenError("search/issues returned an unexpected response")
    return items


def _require_list(value, endpoint):
    """Fail closed: a check that got a non-list response must error, not guess."""
    if not isinstance(value, list):
        raise TakenError(f"`gh api {endpoint}` returned an unexpected response")
    return value


def _cache_path():
    """Location of the API response cache. Overridable via TAKEN_CACHE_DIR."""
    base = os.environ.get("TAKEN_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "taken"
    )
    return os.path.join(base, "api_cache.json")


def _cache_key(endpoint, params):
    parts = [endpoint.lstrip("/")]
    for key in sorted(params or {}):
        parts.append(f"{key}={(params or {})[key]}")
    return "|".join(parts)


def _cache_read(key):
    try:
        with _CACHE_LOCK:
            with open(_cache_path(), encoding="utf-8") as fh:
                entries = json.load(fh)
        entry = entries.get(key)
        if not entry:
            return None
        if time.time() - entry["fetched_at"] > CACHE_TTL_SECONDS:
            return None
        return entry["data"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _cache_write(key, data):
    try:
        path = _cache_path()
        with _CACHE_LOCK:
            try:
                with open(path, encoding="utf-8") as fh:
                    entries = json.load(fh)
            except (OSError, ValueError):
                entries = {}
            entries[key] = {"fetched_at": time.time(), "data": data}
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # Atomic write: concurrent threads must never see a half-written cache.
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".cache-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(entries, fh)
                os.replace(tmp, path)
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except OSError:
        pass  # the cache must never break the tool


def gh_api(endpoint, params=None):
    """GET a GitHub API endpoint via `gh api` and return parsed JSON."""
    key = _cache_key(endpoint, params)
    if _CACHE_ENABLED:
        cached = _cache_read(key)
        if cached is not None:
            return cached
    cmd = ["gh", "api", endpoint.lstrip("/")]
    for key_param, value in (params or {}).items():
        cmd.extend(["-f", f"{key_param}={value}"])
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
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise TakenError(f"`gh api {endpoint}` did not return JSON")
    if _CACHE_ENABLED:
        _cache_write(key, data)
    return data


def check_issue(owner, repo, number):
    """Fetch the basic facts about an issue."""
    endpoint = f"repos/{owner}/{repo}/issues/{number}"
    data = _require_dict(gh_api(endpoint), endpoint)
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
    endpoint = f"repos/{owner}/{repo}/issues/{number}/timeline"
    events = _require_list(gh_api(endpoint, {"per_page": "100"}), endpoint)
    linked = []
    seen = set()
    for event in events:
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
    if not isinstance(comments, list):
        raise TakenError("comment scan got an unexpected response")
    hits = []
    me_lower = (me or "").lower()
    for comment in comments:
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


def fetch_comments(owner, repo, number):
    """Fetch raw issue comments (cached like everything else)."""
    endpoint = f"repos/{owner}/{repo}/issues/{number}/comments"
    return _require_list(gh_api(endpoint, {"per_page": "100"}), endpoint)


def check_claimants(owner, repo, number, me=None):
    """Fetch issue comments and scan them for claimant language."""
    return find_claimant_hits(fetch_comments(owner, repo, number), me=me)


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
        endpoint = f"repos/{owner}/{repo}/contents/{path}"
        try:
            data = gh_api(endpoint)
        except NotFoundError:
            continue
        # A path that exists but is unreadable is a real failure, not "no policy".
        _require_dict(data, endpoint)
        try:
            raw = base64.b64decode(data.get("content") or "")
        except Exception as exc:
            raise TakenError(f"could not decode {path}: {exc}")
        text = raw.decode("utf-8", errors="replace")
        verdict, snippet = classify_policy(text)
        return {"verdict": verdict, "snippet": snippet, "source": path}
    return {"verdict": "none-found", "snippet": "", "source": None}


def list_open_issues(owner, repo, limit=20, label=None):
    """List open issues (not PRs) for a repo, most recently updated first."""
    endpoint = f"repos/{owner}/{repo}/issues"
    params = {"state": "open", "per_page": "100", "sort": "updated", "direction": "desc"}
    if label:
        params["labels"] = label
    found = []
    page = 1
    while len(found) < limit:
        params["page"] = str(page)
        items = _require_list(gh_api(endpoint, params), endpoint)
        if not items:
            break
        for item in items:
            if "pull_request" in item:
                continue
            found.append((owner, repo, item["number"]))
            if len(found) >= limit:
                break
        if len(items) < 100:
            break
        page += 1
    return found


def check_repo_health(owner, repo, window_days=HEALTH_WINDOW_DAYS):
    """Check recent pushes and merged PRs as a rough activity signal."""
    endpoint = f"repos/{owner}/{repo}"
    data = _require_dict(gh_api(endpoint), endpoint)
    pushed_at = data.get("pushed_at") or ""
    pushed_recently = False
    if pushed_at:
        pushed_dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
        pushed_recently = datetime.now(timezone.utc) - pushed_dt <= timedelta(days=window_days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    recent_merges = 0
    for page in (1, 2):
        pulls_endpoint = f"repos/{owner}/{repo}/pulls"
        prs = _require_list(
            gh_api(
                pulls_endpoint,
                {
                    "state": "closed",
                    "per_page": "50",
                    "page": str(page),
                    "sort": "updated",
                    "direction": "desc",
                },
            ),
            pulls_endpoint,
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


def run_checks(owner, repo, number, me=None):
    """Run the full read-only check suite on one issue; return findings."""
    issue = check_issue(owner, repo, number)
    return {
        "target": f"{owner}/{repo}#{number}",
        "issue": issue,
        "linked_prs": check_timeline(owner, repo, number),
        "claimants": check_claimants(owner, repo, number, me=me),
        "ai_policy": check_ai_policy(owner, repo),
        "repo_health": check_repo_health(owner, repo),
    }
