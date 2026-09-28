"""Regenerate the vendored resource blocks in the Homebrew tap formula.

Usage: rewrite_tap_formula.py <formula-path> <resources-json> <sdist-url> <sdist-sha256> <version>

Replaces every existing `resource ... end` block with the freshly resolved
closure (sorted by name), refreshes the taken sdist url/sha256, and syncs the
`--version` test assertion. Everything else (depends_on lines, install, test
block) is left untouched.

Fails loudly if the formula has no resource-block region to replace and no
depends_on anchor to insert after, or if the post-rewrite sanity checks fail.
"""

import json
import re
import sys

formula_path, resources_path, sdist_url, sdist_sha, version = sys.argv[1:6]

resources = json.load(open(resources_path))  # [[name, ver, url, sha256, uploaded], ...]
resources.sort(key=lambda r: r[0].lower())


def resource_block(name, ver, url, sha):
    return f'  resource "{name}" do\n    url "{url}"\n    sha256 "{sha}"\n  end\n'


new_blocks = (
    "\n".join(resource_block(n, v, u, s) for n, v, u, s, _ in resources).rstrip("\n") + "\n"
)

text = open(formula_path).read()

# 1. Replace the resource region (first block start -> last block end).
block_re = re.compile(r'^  resource "[^"]+" do\n(?:    .*\n)*?  end\n', re.M)
matches = list(block_re.finditer(text))
if matches:
    start, end = matches[0].start(), matches[-1].end()
    text = text[:start] + new_blocks + text[end:]
else:
    # No resources yet: insert after the last depends_on line.
    dep_re = re.compile(r"^  depends_on .*\n", re.M)
    dep_matches = list(dep_re.finditer(text))
    if not dep_matches:
        sys.exit("no resource blocks and no depends_on anchor found; refusing to guess")
    pos = dep_matches[-1].end()
    text = text[:pos] + "\n" + new_blocks + text[pos:]

# 2. Refresh the taken sdist url/sha256 (first url/sha256 pair in the file,
#    which belongs to the formula itself, not to a resource block).
lines = text.splitlines(keepends=True)
out = []
replaced_url = replaced_sha = False
for line in lines:
    stripped = line.rstrip("\n")
    if (not replaced_url) and re.match(r'^  url ".*taken.gh-.*"$', stripped):
        out.append(f'  url "{sdist_url}"\n')
        replaced_url = True
    elif replaced_url and (not replaced_sha) and stripped.startswith('  sha256 "'):
        out.append(f'  sha256 "{sdist_sha}"\n')
        replaced_sha = True
    else:
        out.append(re.sub(r'assert_match "taken [^"]+"', f'assert_match "taken {version}"', line))
if not (replaced_url and replaced_sha):
    sys.exit("formula url/sha256 lines not found")
text = "".join(out)

# 3. Sanity checks: nothing structural was lost.
checks = [
    ('depends_on "python@3.13"' in text, "python depends_on missing"),
    ('depends_on "rust" => :build' in text, "rust build depends_on missing"),
    ("virtualenv_install_with_resources" in text, "install block missing"),
    ('pipe_output("#{bin}/taken-mcp", "", 0)' in text, "taken-mcp test missing"),
]
found_blocks = len(block_re.findall(text))
checks.append(
    (
        found_blocks == len(resources),
        f"resource count mismatch: found {found_blocks}, expected {len(resources)}",
    )
)
failed = [msg for ok, msg in checks if not ok]
if failed:
    sys.exit("sanity check failed: " + "; ".join(failed))

open(formula_path, "w").write(text)
print(f"{formula_path}: {len(resources)} resources regenerated for taken {version}")
