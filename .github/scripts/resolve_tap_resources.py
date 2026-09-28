"""Resolve the Homebrew formula's vendored dependency closure for taken-gh.

Target: taken-gh==<version> on CPython 3.13, macOS + Linux (union of markers).

Usage: resolve_tap_resources.py <version> <output-json>

Homebrew's `virtualenv_install_with_resources` installs every resource with
`pip --no-deps`, so the formula must vendor the full transitive closure as
pinned sdist resources. This resolver:

- expands requested extras (e.g. `pyjwt[crypto]` pulls in cffi, cryptography,
  pycparser) instead of silently dropping them,
- intersects every parent's SpecifierSet per package, then picks the max
  version satisfying the intersection (fixpoint iteration, not greedy),
- gates each pick on Requires-Python allowing 3.13,
- normalizes names per PEP 503,
- evaluates markers against complete explicit envs for 4 targets
  (darwin/arm64, darwin/x86_64, linux/x86_64, linux/aarch64, CPython 3.13)
  so the host Python never leaks into the result.

Output JSON: [[name, version, sdist_url, sha256, upload_iso]] sorted by name.
Hard failures (no sdist, unsatisfiable constraints) raise; sdists uploaded
within Homebrew's 14-day cooldown are printed as warnings.
"""

import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

VERSION = sys.argv[1]
OUT_PATH = sys.argv[2]

UA = {"User-Agent": "taken-tap-resource-resolver/2.0"}
_cache = {}


def get_json(url):
    if url not in _cache:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            _cache[url] = json.load(r)
        time.sleep(0.15)
    return _cache[url]


def pypi_json(name, version=None):
    url = (
        f"https://pypi.org/pypi/{name}/{version}/json"
        if version
        else f"https://pypi.org/pypi/{name}/json"
    )
    return get_json(url)


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


PY = "3.13"
TARGET_ENVS = [
    dict(
        python_version=PY,
        python_full_version="3.13.7",
        sys_platform="darwin",
        platform_system="Darwin",
        platform_machine="arm64",
        os_name="posix",
        platform_python_implementation="CPython",
        implementation_name="cpython",
    ),
    dict(
        python_version=PY,
        python_full_version="3.13.7",
        sys_platform="darwin",
        platform_system="Darwin",
        platform_machine="x86_64",
        os_name="posix",
        platform_python_implementation="CPython",
        implementation_name="cpython",
    ),
    dict(
        python_version=PY,
        python_full_version="3.13.7",
        sys_platform="linux",
        platform_system="Linux",
        platform_machine="x86_64",
        os_name="posix",
        platform_python_implementation="CPython",
        implementation_name="cpython",
    ),
    dict(
        python_version=PY,
        python_full_version="3.13.7",
        sys_platform="linux",
        platform_system="Linux",
        platform_machine="aarch64",
        os_name="posix",
        platform_python_implementation="CPython",
        implementation_name="cpython",
    ),
]


def marker_hits_any(req, extras):
    """True if req's marker hits on any target env, for any of the given
    extra values (None = no extra requested, the default install case)."""
    if req.marker is None:
        return True
    for extra in extras:
        for env in TARGET_ENVS:
            e = dict(env)
            if extra is None:
                e.pop("extra", None)
            else:
                e["extra"] = extra
            if req.marker.evaluate(e):
                return True
    return False


def allows_py313(requires_python):
    if not requires_python:
        return True
    return Version("3.13.0") in SpecifierSet(requires_python)


# ---- phase 1: collect constraints + extras to a fixpoint ----
constraints = {}  # norm name -> list of SpecifierSet
extras = {}  # norm name -> set of extras
real_names = {}  # norm name -> canonical project name from PyPI
origins = {}  # norm name -> list of 'parent -> reqstring' for the report


def add_constraint(req, origin):
    name = norm(req.name)
    real_names.setdefault(name, pypi_json(req.name)["info"]["name"])
    constraints.setdefault(name, [])
    if str(req.specifier):
        constraints[name].append(req.specifier)
    for x in req.extras:
        extras.setdefault(name, set()).add(x)
    origins.setdefault(name, []).append(f"{origin} -> {str(req)}")
    return name


def expand_package(name, ver):
    """Queue this version's requires_dist, honoring requested extras."""
    vinfo = pypi_json(real_names[name], ver)["info"]
    wanted_extras = extras.get(name, set())
    for rd in vinfo.get("requires_dist") or []:
        sub = Requirement(rd)
        if sub.url:
            raise RuntimeError(f"direct URL requirement not supported: {rd}")
        # include if marker hits with no extra OR with any requested extra
        if marker_hits_any(sub, [None] + sorted(wanted_extras)):
            add_constraint(sub, f"{real_names[name]}=={ver}")


root = pypi_json("taken-gh", VERSION)["info"]
assert root["version"] == VERSION, root["version"]
assert allows_py313(root["requires_python"]), root["requires_python"]
for rd in root["requires_dist"] or []:
    add_constraint(Requirement(rd), f"taken-gh=={VERSION}")

picked = {}  # norm name -> version string
changed = True
rounds = 0
while changed:
    rounds += 1
    if rounds > 50:
        raise RuntimeError("fixpoint did not converge")
    changed = False
    for name in list(constraints):
        spec = SpecifierSet()
        for s in constraints[name]:
            spec &= s
        data = pypi_json(real_names[name])
        cands = []
        for v, files in data["releases"].items():
            try:
                ver = Version(v)
            except Exception:
                continue
            if ver.is_prerelease or ver.is_devrelease or not files:
                continue
            if ver not in spec:
                continue
            cands.append(ver)
        if not cands:
            raise RuntimeError(
                f"NO version of {real_names[name]} satisfies {spec} (from: {origins[name]})"
            )
        best = str(max(cands))
        # per-version Requires-Python gate on the chosen version
        vinfo = pypi_json(real_names[name], best)["info"]
        if not allows_py313(vinfo.get("requires_python")):
            raise RuntimeError(
                f"{real_names[name]}=={best} requires {vinfo.get('requires_python')}, excludes 3.13"
            )
        if picked.get(name) != best:
            picked[name] = best
            changed = True
        expand_package(name, best)

print(f"converged in {rounds} rounds; {len(picked)} packages")
now = datetime.now(timezone.utc)
results = []
for name in sorted(picked):
    real, ver = real_names[name], picked[name]
    data = pypi_json(real)
    sdists = [u for u in data["releases"][ver] if u["packagetype"] == "sdist"]
    if not sdists:
        raise RuntimeError(f"NO SDIST for {real}=={ver} -- formula cannot vendor it")
    s = sdists[0]
    up = s.get("upload_time_iso_8601") or s.get("upload_time")
    age_days = (now - datetime.fromisoformat(up)).days if up else None
    flag = ""
    if age_days is not None and age_days < 14:
        flag = "  <-- WARNING: uploaded <14d ago, brew --uploaded-prior-to=P14D may reject"
    print(f"  {real}=={ver}  age={age_days}d{flag}")
    print(f"      {s['url']}")
    results.append((norm(real), ver, s["url"], s["digests"]["sha256"], up))

with open(OUT_PATH, "w") as f:
    json.dump(results, f, indent=1)
print(f"wrote {OUT_PATH}")
