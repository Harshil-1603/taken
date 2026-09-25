"""Command-line interface for taken."""

import argparse
import json
import re
import sys

from taken import __version__, checks
from taken.verdict import CAUTION, GO, TAKEN, decide

EXIT_CODES = {GO: 0, TAKEN: 1, CAUTION: 2}

URL_RE = re.compile(r"^https?://github\.com/([^/\s]+)/([^/\s]+)/issues/(\d+)/?$")
SHORT_RE = re.compile(r"^([^/\s#]+)/([^/\s#]+)#(\d+)$")
PATH_RE = re.compile(r"^([^/\s]+)/([^/\s]+)/issues/(\d+)/?$")


def parse_target(text):
    """Parse `owner/repo#123` or a GitHub issue URL into (owner, repo, number)."""
    text = text.strip()
    for pattern in (URL_RE, SHORT_RE, PATH_RE):
        match = pattern.match(text)
        if match:
            owner, repo, number = match.groups()
            return owner, repo, int(number)
    return None


def build_parser():
    parser = argparse.ArgumentParser(
        prog="taken",
        description="Check whether a GitHub issue is already taken before you volunteer for it.",
    )
    parser.add_argument("target", help="owner/repo#123 or a GitHub issue URL")
    parser.add_argument("--json", action="store_true", help="print the full findings as JSON")
    parser.add_argument(
        "--me",
        metavar="LOGIN",
        default=None,
        help="your GitHub login; your own comments are ignored in the claimant scan",
    )
    parser.add_argument("--version", action="version", version=f"taken {__version__}")
    return parser


def run_checks(owner, repo, number, me):
    issue = checks.check_issue(owner, repo, number)
    return {
        "target": f"{owner}/{repo}#{number}",
        "issue": issue,
        "linked_prs": checks.check_timeline(owner, repo, number),
        "claimants": checks.check_claimants(owner, repo, number, me=me),
        "ai_policy": checks.check_ai_policy(owner, repo),
        "repo_health": checks.check_repo_health(owner, repo),
    }


def format_human(findings, verdict, reasons):
    issue = findings["issue"]
    health = findings["repo_health"]
    policy = findings["ai_policy"]
    lines = [
        f"taken? {findings['target']}",
        f"verdict: {verdict}",
        "",
        f'  issue: {issue["state"]}, "{issue["title"]}"',
        f"         {issue['url']} ({issue['comment_count']} comments)",
    ]
    if findings["linked_prs"]:
        for pr in findings["linked_prs"]:
            if pr["state"] == "open":
                status = "open"
            elif pr["merged"]:
                status = "merged"
            else:
                status = "closed"
            lines.append(f'  linked PR: #{pr["number"]} "{pr["title"]}" ({status})')
            lines.append(f"             {pr['url']}")
    else:
        lines.append("  linked PRs: none found in timeline")
    if issue["assignees"]:
        lines.append(f"  assignees: {', '.join(issue['assignees'])}")
    else:
        lines.append("  assignees: none")
    if findings["claimants"]:
        for hit in findings["claimants"]:
            lines.append(
                f'  claimant: {hit["author"]} on {hit["date"]} (matched "{hit["pattern"]}")'
            )
            lines.append(f'            "{hit["snippet"]}"')
    else:
        lines.append("  claimants: none found in comments")
    if policy["source"]:
        lines.append(f"  AI policy: {policy['verdict']} ({policy['source']})")
        if policy["snippet"]:
            lines.append(f'             "{policy["snippet"]}"')
    else:
        lines.append("  AI policy: none found (no CONTRIBUTING file)")
    lines.append(
        f"  repo health: pushed {health['pushed_at'] or 'unknown'}, "
        f"{health['recent_merges']} PRs merged in last 30 days, "
        f"{health['stars']} stars"
    )
    lines.append("")
    lines.append("why:")
    for reason in reasons:
        lines.append(f"  - {reason}")
    return "\n".join(lines)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    parsed = parse_target(args.target)
    if not parsed:
        print(
            f"error: could not parse {args.target!r}; use owner/repo#123 or an issue URL",
            file=sys.stderr,
        )
        return 3
    owner, repo, number = parsed
    try:
        findings = run_checks(owner, repo, number, me=args.me)
    except checks.TakenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    verdict, reasons = decide(findings)
    if args.json:
        print(json.dumps({"verdict": verdict, "reasons": reasons, "findings": findings}, indent=2))
    else:
        print(format_human(findings, verdict, reasons))
    return EXIT_CODES[verdict]


if __name__ == "__main__":
    sys.exit(main())
