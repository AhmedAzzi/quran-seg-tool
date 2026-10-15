#!/usr/bin/env python3
"""Commit helper for reviewers who don't use git daily.

Shows what changed (per edition + pages), suggests a professional commit
message like "Update pages: asbahani 483, 485-486, 518-519; hafs 446",
and optionally commits + pushes it.

Usage (from the repo root):
    py tool/commit_help.py                 # just show the suggestion
    py tool/commit_help.py --commit        # stage + commit with suggestion
    py tool/commit_help.py --commit --push # ... and push to origin/main
    py tool/commit_help.py --commit --message "my own message"

Only meaningful files are staged (edited boxes, logs, rebuilt bundles,
tool changes). Local backups (boxes_raw_bak/, bak/) are never committed.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EDITIONS = ("warsh", "hafs", "asbahani")
PAGE_RE = re.compile(r"^(?:editions|web)/([^/]+)/(?:boxes_raw|review|tokens)/(\d+)\.(?:json|js)$")
SKIP_RE = re.compile(r"(^|/)(bak|boxes_raw_bak)(/|$)")


def git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout


def parse_status():
    out = git("status", "--porcelain")
    changed, untracked = [], []
    for line in out.splitlines():
        if not line.strip():
            continue
        code, path = line[:2], line[3:].strip().strip('"')
        (untracked if code == "??" else changed).append(path)
    return changed, untracked


def page_sets(paths):
    found = {ed: set() for ed in EDITIONS}
    for p in paths:
        m = PAGE_RE.match(p)
        if m and m.group(1) in found:
            found[m.group(1)].add(int(m.group(2)))
    return {ed: sorted(s) for ed, s in found.items() if s}


def compress(nums):
    """[483,485,486,518,519] -> '483, 485-486, 518-519'"""
    if not nums:
        return ""
    parts, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
        else:
            parts.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = n
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(parts)


def suggest(changed, untracked):
    pages = page_sets(changed + untracked)
    if not pages and not changed and not untracked:
        return None, None
    bits = [f"{ed} {compress(ns)}" for ed, ns in pages.items()]
    subject = "Update pages: " + "; ".join(bits) if bits else "Update review data"
    tool_files = sorted({p for p in changed + untracked
                         if p in ("web/mushaf.html", "web/serve.py")
                         or p.startswith("tool/") or p.startswith(".github/")})
    if tool_files:
        subject += f" + tool update ({', '.join(Path(p).name for p in tool_files)})"
    body_lines = [f"- {ed}: pages {compress(ns)}" for ed, ns in pages.items()]
    if tool_files:
        body_lines.append("- tool: " + ", ".join(tool_files))
    return subject, "\n".join(body_lines)


def stage_all(changed, untracked):
    keep = [p for p in untracked if not SKIP_RE.search(p)]
    skipped = [p for p in untracked if SKIP_RE.search(p)]
    if changed:
        git("add", "--", *changed)
    if keep:
        git("add", "--", *keep)
    return skipped


def main(argv):
    do_commit = "--commit" in argv
    do_push = "--push" in argv
    msg = None
    if "--message" in argv:
        i = argv.index("--message")
        if i + 1 >= len(argv):
            sys.exit("--message needs a value")
        msg = argv[i + 1]

    changed, untracked = parse_status()
    if not changed and not untracked:
        print("Nothing to commit — working tree is clean.")
        return

    subject, body = suggest(changed, untracked)
    if msg:
        subject, body = msg, ""
    print("Changed files:")
    for p in changed:
        print(f"  M {p}")
    for p in untracked:
        tag = "skip (backup)" if SKIP_RE.search(p) else "new"
        print(f"  ? {p}   [{tag}]")
    print(f'\nSuggested message:\n  {subject}')
    if body:
        print("  " + body.replace("\n", "\n  "))

    if not do_commit:
        print('\nTo commit with this message, run:\n'
              '  py tool/commit_help.py --commit\n'
              'Or commit yourself:\n'
              f'  git add -u  (plus new files except *_bak backups)\n'
              f'  git commit -m "{subject}"\n'
              "  git push origin main")
        return

    skipped = stage_all(changed, untracked)
    args = ["commit", "-m", subject]
    if body:
        args += ["-m", body]
    git(*args)
    print(f"\nCommitted: {subject}")
    if skipped:
        print("Left uncommitted (local backups): " + ", ".join(skipped))
    if do_push:
        print(git("push", "origin", "main"))
        print("Pushed — the packs workflow rebuilds the edition zips.")


if __name__ == "__main__":
    main(sys.argv[1:])
