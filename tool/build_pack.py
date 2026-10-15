#!/usr/bin/env python3
"""Assemble one on-demand mushaf pack zip with the packs-v2 layout.

Usage:
    python3 tool/build_pack.py <warsh|hafs|asbahani>
        [--out <file>] [--base-url <url>]

Layout at zip root (must match what the app's EditionPackController
expects — see Husn-el-Muslim lib/features/mushaf/data/edition_packs.dart):
    data/quran.db, data/page_boxes.json, data/page_markers.json,
    data/timings.json, data/ayah_index.json, data/overlay_texts.json,
    pages/001.jpg ... pages/604.jpg

Inputs, in priority order:
  1. This repo: editions/<ed>/pages/*.jpg plus any of page_boxes.json,
     page_markers.json, timings.json present there (reviewer corrections
     flow through from here on every push).
  2. Base pack URL (default: the matching Husn-el-Muslim packs-v2 asset)
     for everything else — quran.db, ayah_index.json, overlay_texts.json,
     and any data file missing locally. Downloaded to a temp dir, never
     committed.

The script fails fast on missing/empty inputs and verifies the finished
zip (610 entries, forward slashes, JPEG magic, quran.db opens with the
expected table counts) before writing it.
"""

import os
import sqlite3
import sys
import tempfile
import urllib.request
import zipfile

EDITIONS = ("warsh", "hafs", "asbahani")
BASE_URL = (
    "https://github.com/AhmedAzzi/Husn-el-Muslim"
    "/releases/download/packs-v2/{ed}.zip"
)
REPO_DATA_FILES = ("page_boxes.json", "page_markers.json", "timings.json")
BASE_DATA_FILES = (
    "quran.db",
    "ayah_index.json",
    "overlay_texts.json",
)
EXPECTED_WORDS = {
    "warsh": 77860,
    "hafs": None,  # checked only for presence, counts vary by edition
    "asbahani": None,
}


def _get(url, dst):
    req = urllib.request.Request(url, headers={"User-Agent": "quran-seg-tool"})
    with urllib.request.urlopen(req) as r, open(dst, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                return
            f.write(chunk)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in EDITIONS:
        sys.exit(f"usage: build_pack.py <{'|'.join(EDITIONS)}> [--out f] [--base-url u]")
    ed = sys.argv[1]

    def opt(name, fallback):
        if name not in sys.argv:
            return fallback
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else fallback

    out = opt("--out", f"{ed}.zip")
    base_url = opt("--base-url", BASE_URL.format(ed=ed))

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ws = os.path.join(root, "editions", ed)

    # 1. Staged entries: (arc_name, bytes-or-disk-path).
    staged = {}

    pages_dir = os.path.join(ws, "pages")
    for i in range(1, 605):
        # Workspace scans use plain numbers (1.jpg); packs and the app
        # use zero-padded names (001.jpg).
        p = os.path.join(pages_dir, f"{i}.jpg")
        if not os.path.isfile(p) or os.path.getsize(p) == 0:
            sys.exit(f"MISSING OR EMPTY: {p}")
        staged[f"pages/{i:03d}.jpg"] = p

    tmp = tempfile.mkdtemp(prefix=f"{ed}_base")
    base_zip_path = os.path.join(tmp, "base.zip")
    base = None

    def base_entry(name):
        nonlocal base
        if base is None:
            print(f"fetching base pack {base_url} ...")
            _get(base_url, base_zip_path)
            base = zipfile.ZipFile(base_zip_path)
        with base.open(f"data/{name}") as f:
            return f.read()

    for name in REPO_DATA_FILES + BASE_DATA_FILES:
        local = os.path.join(ws, name)
        if name in REPO_DATA_FILES and os.path.isfile(local):
            if os.path.getsize(local) == 0:
                sys.exit(f"EMPTY: {local}")
            print(f"data/{name}: from repo")
            staged[f"data/{name}"] = local
        else:
            print(f"data/{name}: from base pack")
            staged[f"data/{name}"] = ("__bytes__", base_entry(name))

    # 2. Write the zip.
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for arc in sorted(staged):
            src = staged[arc]
            if isinstance(src, tuple):
                z.writestr(arc, src[1])
            else:
                z.write(src, arc)
    print(f"wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB)")

    # 3. Verify like the app's readiness gate + reader queries.
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert len(names) == 610, f"expected 610 entries, got {len(names)}"
        assert all("\\" not in n for n in names), "backslashes in zip paths"
        for want in (
            "data/quran.db",
            "data/page_boxes.json",
            "data/page_markers.json",
            "data/timings.json",
            "data/ayah_index.json",
            "data/overlay_texts.json",
            "pages/001.jpg",
            "pages/604.jpg",
        ):
            assert want in names, f"missing {want}"
        for n in ("pages/001.jpg", "pages/020.jpg"):
            assert z.read(n)[:2] == b"\xff\xd8", f"{n} is not a JPEG"
        db_path = os.path.join(tmp, "quran.db")
        with open(db_path, "wb") as f:
            f.write(z.read("data/quran.db"))
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    words = db.execute("SELECT COUNT(*) FROM words").fetchone()[0]
    ayahs = db.execute("SELECT COUNT(*) FROM ayahs").fetchone()[0]
    assert words > 0 and ayahs > 0
    exp = EXPECTED_WORDS[ed]
    if exp is not None:
        assert words == exp, f"words {words} != expected {exp}"
    assert db.execute(
        "SELECT COUNT(*) FROM words WHERE page='020'"
    ).fetchone()[0] > 0
    print(f"verified: {words} words, {ayahs} ayahs, page 020 readable")
    print("PACK OK")


if __name__ == "__main__":
    main()
