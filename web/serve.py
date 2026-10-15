#!/usr/bin/env python3
"""Local companion server for web/mushaf.html one-click save.

Run:   py web/serve.py            (or double-click web/serve.bat)
Open:  http://127.0.0.1:8765/web/mushaf.html   (or keep using the file directly)

Why: browsers give web pages no silent path to local folders. This
localhost-only server accepts the app's single Save button:
  PUT  /api/save/<edition>/<n>.json  edited boxes -> boxes_raw
  POST /api/finalize  {edition, pages}  rebuild the web bundle slices
  POST /api/export    {edition, pages}  patch this repo's pack inputs
No server -> the app cannot save; start web/serve.bat first.

Corrections: every applied save appends one line to
editions/<edition>/corrections_log.jsonl ({at, page, tokens}) — that file
plus editions/<edition>/boxes_raw/ IS the reviewer's deliverable.

Export: POST /api/export converts the given pages (pixel boxes ->
Flutter fractions, word keys, marker spots) and patches
editions/<edition>/page_boxes.json + page_markers.json, with whole-file
backups under bak/app_export/ (local only, git-ignored) and a watermark at
editions/<edition>/last_app_export.json. The next push rebuilds the
downloadable packs automatically.

Safety: binds 127.0.0.1 only; writes ONLY editions/<ed>/boxes_raw/<digits>.json
(path-traversal impossible by construction: edition allow-listed, page must
be digits.json); 5MB body cap; no-store caching so rebuilt bundles are fresh.
"""

import datetime
import json
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # web/ regroup: serve project root
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
EDITIONS = ('warsh', 'hafs', 'asbahani')
NAME_RE = re.compile(r'^\d+\.json$')
KEY_RE = re.compile(r'^(\d+):(\d+):(\d+)$')
MAX_BODY = 5_000_000
SERVER_VERSION = 7  # 7 = single Save button: save + finalize + export


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def edition_box_dir(ed):
    return ROOT / 'editions' / ed / 'boxes_raw'


def backup_original(ed, name):
    """First-write-wins backup of editions/<ed>/boxes_raw/<name> (pre-edit original)."""
    bak = ROOT / 'editions' / ed / 'boxes_raw_bak' / name
    src = edition_box_dir(ed) / name
    if not bak.exists() and src.exists():
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_bytes(src.read_bytes())


def log_correction(ed, name, n_tokens):
    """Append one line per applied save: the collectable corrections record."""
    log = ROOT / 'editions' / ed / 'corrections_log.jsonl'
    with log.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'at': utcnow(), 'page': int(name[:-5]),
                            'tokens': n_tokens}, ensure_ascii=False) + '\n')


def rebuild_bundle_slice(ed, n):
    """Refresh web/<ed>/tokens/<n>.js + review/<n>.js from the boxes files."""
    src = ROOT / 'editions' / ed
    out = ROOT / 'web' / ed
    jf = src / 'boxes_final' / f'{n}.json'
    if jf.exists():
        toks = json.loads(jf.read_text(encoding='utf-8'))
        (out / 'tokens').mkdir(parents=True, exist_ok=True)
        (out / 'tokens' / f'{n}.js').write_text(
            'window.__PAGE_TOKENS = ' + json.dumps(toks, ensure_ascii=False) + ';',
            encoding='utf-8')
    rj = src / 'boxes_raw' / f'{n}.json'
    if rj.exists():
        rt = json.loads(rj.read_text(encoding='utf-8'))
        (out / 'review').mkdir(parents=True, exist_ok=True)
        (out / 'review' / f'{n}.js').write_text(
            'window.__PAGE_TOKENS = ' + json.dumps(rt, ensure_ascii=False) + ';',
            encoding='utf-8')


def finalize_pages(ed, pages):
    """One-button pipeline for applied pages: rebuild the web bundle slices
    the app reloads. Meaning reassignment + oracle recompute stay on the
    owner's side (pipeline scripts). Failures are per-page, never raised."""
    results = {}
    for n in pages:
        stem = str(n)
        try:
            rebuild_bundle_slice(ed, n)
            results[stem] = {'ok': True, 'rebuilt': 'tokens+review'}
        except Exception as e:  # noqa: BLE001 -- per-page isolation
            results[stem] = {'ok': False, 'error': str(e)[:200]}
    print(f'finalized {ed} pages {pages}: ' + json.dumps(results), flush=True)
    return {'ok': True, 'pages': results}


# ---------- export to pack inputs (web "Save" button -> repo pack data) ----------
# Bridges the review loop into the downloadable packs WITHOUT leaving this
# repo: the exact pages the reviewer just saved are converted (pixel -> app
# fractions, word-key assignment mirroring mushaf.html composeMap, marker
# spots mirroring build_markers.py) and patched into this repo's pack
# inputs:
#   editions/<ed>/page_boxes.json
#   editions/<ed>/page_markers.json
# The next push rebuilds + republishes the packs automatically
# (see .github/workflows/build-packs.yml).
# Corrected-only: the client sends the exact pages it just saved.
# Whole-file backups land
# in bak/app_export/ (git-ignored, local only) before any write; per-page
# isolation (one bad page never blocks the rest, watermark advances only
# for ok pages).
R_LO, R_HI = 0.021, 0.036  # marker radius clamp, fraction of page height


def jpeg_size(path):
    """Image dimensions without Pillow: scan JPEG SOF markers (stdlib)."""
    try:
        with open(path, 'rb') as f:
            d = f.read()
    except OSError:
        return None
    if len(d) < 4 or d[0] != 0xFF or d[1] != 0xD8:
        return None
    import struct
    i = 2
    n = len(d)
    while i + 4 <= n:
        if d[i] != 0xFF:
            i += 1
            continue
        m = d[i + 1]
        if m in (0xC0, 0xC1, 0xC2, 0xC3):
            h = struct.unpack('>H', d[i + 5:i + 7])[0]
            w = struct.unpack('>H', d[i + 7:i + 9])[0]
            return (w, h) if w > 0 and h > 0 else None
        if m in (0xD8, 0xD9) or (0xD0 <= m <= 0xD7) or m == 0x01:
            i += 2
            continue
        if i + 4 > n:
            return None
        ln = struct.unpack('>H', d[i + 2:i + 4])[0]
        if ln < 2:
            return None
        i += 2 + ln
    return None


def _cmp_word_key(a, b):
    xa = [int(x) for x in a.split(':')]
    xb = [int(x) for x in b.split(':')]
    return (xa > xb) - (xa < xb)


def compose_map_keys(n, span_words, span_first, span_last, span_skip, anchors):
    """Port of mushaf.html composeMap: sequential word keys for n word
    boxes (already sorted y0, then x0 descending). Base maps from the
    page start; each anchor restarts its tail. Returns [key|None]."""
    out = [None] * n
    if not span_words or not span_first:
        return out
    skip = set(span_skip or [])
    idx_of = {w['key']: i for i, w in enumerate(span_words)}
    eff = [{'box': 0, 'key': span_first}] + sorted(
        anchors or [], key=lambda a: a['box'])
    for i in range(n):
        a = eff[0]
        for an in eff:
            if an['box'] <= i:
                a = an
            else:
                break
        if not a or not a.get('key'):
            continue
        si = idx_of.get(a['key'])
        if si is None:
            continue
        steps = i - a['box']
        for j in range(si, len(span_words)):
            k = span_words[j]['key']
            if k in skip:
                continue
            if span_last and _cmp_word_key(k, span_last) > 0:
                break
            if steps == 0:
                out[i] = k
                break
            steps -= 1
    return out


def export_load_raw(ed, n):
    """User-edited geometry first, else the assigned bundle (dry-run /
    unedited pages reproduce production data)."""
    for stage in ('boxes_raw', 'boxes_final'):
        f = ROOT / 'editions' / ed / stage / f'{n}.json'
        if f.exists():
            try:
                toks = json.loads(f.read_text(encoding='utf-8'))
            except ValueError as e:
                return None, None, f'unreadable {stage}/{n}.json: {e}'
            if not isinstance(toks, list):
                return None, None, f'{stage}/{n}.json is not a token list'
            return toks, stage, None
    return None, None, f'no boxes_raw nor boxes_final for page {n}'


def export_assign_page(ed, n, toks):
    """Sort + key word boxes. Returns (keyed, dropped, markers, errors).

    keyed: [(token, key)] for app tap boxes. dropped: [(index, reason)]
    for boxes with no key (span skip overflow, like production's
    key=None tail) — kept out of the app file, reported as warnings.
    """
    try:
        toks = sorted(toks, key=lambda t: (t['y0'], -t['x0']))
    except (KeyError, TypeError) as e:
        return None, None, None, [f'bad token geometry: {e}']
    words = [t for t in toks if t.get('type') == 'word']
    markers = [t for t in toks if t.get('type') == 'marker']
    span_words, first, last, skip, err = page_word_span(ed, n)
    if err:
        return None, None, None, [err]
    anchors = norm_page_anchors(load_anchors(ed).get(str(n)))
    keys = compose_map_keys(len(words), span_words, first, last, skip, anchors)
    errors = []
    seen = set()
    prev = None
    for k in keys:
        if not k:
            continue
        if k in seen:
            errors.append(f'duplicate key {k} — geometry/order broken')
            break
        seen.add(k)
        if prev is not None and _cmp_word_key(k, prev) <= 0:
            errors.append(f'keys not increasing at {k} — order broken')
            break
        prev = k
    if errors:
        return None, None, None, errors
    keyed, dropped = [], []
    for i, (t, k) in enumerate(zip(words, keys)):
        if k:
            keyed.append((t, k))
        else:
            dropped.append((i, 'past span end %s' % last))
    return keyed, dropped, markers, []


def export_build_markers(exp, pkey, keyed_frac, W, H, marker_px):
    """Port of build_markers.py per page: detected (s:0) roundel spots plus
    synthesized (s:1) spots for ayah-ends with no detection. keyed_frac is
    {full_key: fraction_box} from the freshly assigned words."""
    E = (exp.get('pages', {}) or {}).get(pkey)
    exp_ayahs = exp.get('ayahs', {}) or {}
    if not E:
        return None, [f'page {pkey} not in expectations']
    spots = []
    used = set()
    for (s, a, _n) in E.get('ending', []):
        ak = '%d:%d' % (s, a)
        total = (exp_ayahs.get(ak) or {}).get('words', 0)
        if _n != total or total <= 1:
            continue
        wb = keyed_frac.get('%s:%d' % (ak, total))
        if not wb:
            continue
        wx0, wx1 = wb['x'] * W, (wb['x'] + wb['w']) * W
        wy0, wy1 = wb['y'] * H, (wb['y'] + wb['h']) * H
        wh = wy1 - wy0
        best, best_gap = None, None
        for i, m in enumerate(marker_px):
            if i in used:
                continue
            mcx, mcy = (m['x0'] + m['x1']) / 2, (m['y0'] + m['y1']) / 2
            if not (wy0 - 0.4 * wh <= mcy <= wy1 + 0.4 * wh):
                continue
            if mcx >= (wx0 + wx1) / 2:
                continue
            gap = wx0 - m['x1']
            if gap > 2.0 * wh:
                continue
            if best is None or gap < best_gap:
                best, best_gap = i, gap
        if best is not None:
            used.add(best)
            m = marker_px[best]
            mcx = (m['x0'] + m['x1']) / 2 / W
            mcy = (m['y0'] + m['y1']) / 2 / H
            r = max(R_LO, min(R_HI,
                              ((m['x1'] - m['x0']) / W +
                               (m['y1'] - m['y0']) / H) / 4))
            spots.append({'a': ak, 'x': round(mcx, 4), 'y': round(mcy, 4),
                          'r': round(r, 4), 's': 0})
        else:
            cx = max(0.0, min(1.0, (wx0 - 0.5 * wh) / W))
            cy = (wy0 + wy1) / 2 / H
            r = max(R_LO, min(R_HI, 0.62 * wh / H))
            spots.append({'a': ak, 'x': round(cx, 4), 'y': round(cy, 4),
                          'r': round(r, 4), 's': 1})
    return spots, []


def export_watermark_path(ed):
    return ROOT / 'editions' / ed / 'last_app_export.json'


def export_load_watermark(ed):
    try:
        d = json.loads(export_watermark_path(ed).read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def export_load_markers(ed, n, toks, src):
    """Marker geometry for the export: user-edited raw boxes carry their
    own markers; otherwise fall back to the review bundle (same raw
    vintage as boxes_final, which drops marker tokens)."""
    if src == 'boxes_raw':
        return [t for t in toks if t.get('type') == 'marker']
    f = ROOT / 'web' / ed / 'review' / f'{n}.js'
    try:
        txt = f.read_text(encoding='utf-8')
        bundle = json.loads(txt.split('=', 1)[1].rstrip().rstrip(';'))
        if isinstance(bundle, list):
            return [t for t in bundle if t.get('type') == 'marker']
    except (OSError, ValueError, IndexError):
        pass
    return []


def export_build_page(ed, n):
    """Convert one corrected page to app payloads. Returns
    (payload, warnings) or raises ExportError."""
    toks, src, err = export_load_raw(ed, n)
    if err:
        raise ExportError(err)
    img = ROOT / 'editions' / ed / 'pages' / f'{n}.jpg'
    dims = jpeg_size(img)
    if dims is None:
        raise ExportError(f'page image missing/unreadable: {img}')
    W, H = dims
    keyed, dropped, _, errors = export_assign_page(ed, n, toks)
    if errors:
        raise ExportError('; '.join(errors))
    marker_px = export_load_markers(ed, n, toks, src)
    keyed_frac = {}
    boxes = []
    for t, k in keyed:
        try:
            fx0, fy0 = t['x0'] / W, t['y0'] / H
            fw, fh = (t['x1'] - t['x0']) / W, (t['y1'] - t['y0']) / H
        except (KeyError, TypeError, ZeroDivisionError) as e:
            raise ExportError(f'bad geometry on {k}: {e}')
        if fw <= 0 or fh <= 0:
            raise ExportError(f'empty box on {k}')
        if not (0.0 <= fx0 <= 1.0 and 0.0 <= fy0 <= 1.0 and
                fx0 + fw <= 1.0 + 1e-9 and fy0 + fh <= 1.0 + 1e-9):
            raise ExportError(f'box out of page on {k}')
        keyed_frac[k] = {'x': fx0, 'y': fy0, 'w': fw, 'h': fh}
        boxes.append({'key': k, 'x': round(fx0, 4), 'y': round(fy0, 4),
                      'w': round(fw, 4), 'h': round(fh, 4),
                      'flagged': bool(t.get('flagged'))})
    try:
        exp = json.loads((ROOT / 'editions' / ed / 'expectations.json')
                         .read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise ExportError(f'unreadable expectations: {e}')
    spots, merrors = export_build_markers(exp, f'{n:03d}', keyed_frac, W, H,
                                          marker_px)
    if merrors:
        raise ExportError('; '.join(merrors))
    warnings = []
    if dropped:
        warnings.append(
            '%d unkeyed box%s dropped (span overflow: %s)' % (
                len(dropped), '' if len(dropped) == 1 else 'es',
                ', '.join('Box %d' % i for i, _ in dropped[:5])))
    if src != 'boxes_raw' and not marker_px:
        warnings.append('no marker source (review bundle missing) — '
                        'all spots synthesized')
    return {'boxes': boxes, 'markers': spots, 'src': src,
            'W': W, 'H': H}, warnings


class ExportError(Exception):
    pass


def export_pages_to_app(ed, pages):
    """Patch this repo's pack inputs for the given pages.
    Per-page isolation; watermark advances only for ok pages."""
    if ed not in EDITIONS:
        return {'ok': False, 'error': f'unknown edition {ed}', 'pages': {}}
    pack_data = ROOT / 'editions' / ed
    boxes_path = pack_data / 'page_boxes.json'
    markers_path = pack_data / 'page_markers.json'
    if not boxes_path.exists() or not markers_path.exists():
        return {'ok': False,
                'error': f'no pack data for {ed} at {pack_data}',
                'pages': {}}
    try:
        boxes_doc = json.loads(boxes_path.read_text(encoding='utf-8'))
        markers_doc = json.loads(markers_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        return {'ok': False, 'error': f'unreadable pack data: {e}',
                'pages': {}}
    built = {}
    for n in pages:
        pkey = f'{n:03d}'
        try:
            payload, warnings = export_build_page(ed, n)
        except ExportError as e:
            built[str(n)] = {'ok': False, 'error': str(e)[:300]}
            continue
        cur_boxes = ((boxes_doc.get('pages', {}) or {}).get(pkey) or {})\
            .get('boxes', [])
        cur_ayahs = {e.get('a') for e in (markers_doc.get(pkey) or [])
                     if isinstance(e, dict)}
        new_ayahs = {s['a'] for s in payload['markers']}
        if len(payload['boxes']) != len(cur_boxes):
            warnings.append(
                f"boxes {len(cur_boxes)} -> {len(payload['boxes'])}")
        dropped = sorted(cur_ayahs - new_ayahs)
        added = sorted(new_ayahs - cur_ayahs)
        if dropped:
            warnings.append(f"markers dropped: {', '.join(dropped)}")
        if added:
            warnings.append(f"markers added: {', '.join(added)}")
        entry = (boxes_doc.get('pages', {}) or {}).get(pkey)
        if entry is None:
            built[str(n)] = {'ok': False,
                             'error': f'pack data has no page entry {pkey}'}
            continue
        if entry.get('w') != payload['W'] or entry.get('h') != payload['H']:
            warnings.append(
                f"dims data {entry.get('w')}x{entry.get('h')} vs "
                f"image {payload['W']}x{payload['H']}")
        built[str(n)] = {'ok': True, 'src': payload['src'],
                         'boxes': len(payload['boxes']),
                         'markers': len(payload['markers']),
                         'warnings': warnings,
                         '_payload': payload}
    ok_pages = sorted(int(k) for k, v in built.items() if v.get('ok'))
    backup = None
    if ok_pages:
        ts = utcnow().replace(':', '').replace('+', '')
        bak = ROOT / 'bak' / 'app_export' / f'{ed}_{ts}'
        for _ in range(100):
            if not bak.exists():
                break
            bak = ROOT / 'bak' / 'app_export' / f'{ed}_{ts}_{_+1}'
        try:
            bak.mkdir(parents=True, exist_ok=True)
            (bak / 'page_boxes.json').write_bytes(boxes_path.read_bytes())
            (bak / 'page_markers.json').write_bytes(
                markers_path.read_bytes())
            backup = str(bak)
        except OSError as e:
            for k in list(built):
                if built[k].get('ok'):
                    built[k] = {'ok': False,
                                'error': f'backup failed: {e}'[:300]}
            return {'ok': False, 'error': f'backup failed: {e}'[:300],
                    'pages': {k: {kk: vv for kk, vv in v.items()
                                  if not kk.startswith('_')}
                              for k, v in built.items()},
                    'backup': None}
        try:
            for n in ok_pages:
                pkey = f'{n:03d}'
                payload = built[str(n)]['_payload']
                boxes_doc['pages'][pkey]['boxes'] = payload['boxes']
                markers_doc[pkey] = payload['markers']
            meta = boxes_doc.setdefault('meta', {})
            meta['last_app_export'] = {
                'at': utcnow(), 'edition': ed, 'pages': ok_pages}
            # NB: page_boxes.json uses default (', ', ': ') separators —
            # dumping compact would reformat all 604 pages.
            boxes_path.write_text(json.dumps(boxes_doc, ensure_ascii=False),
                                  encoding='utf-8')
            markers_path.write_text(json.dumps(markers_doc,
                                               ensure_ascii=False,
                                               separators=(',', ':')),
                                    encoding='utf-8')
            wm = export_load_watermark(ed)
            have = set(wm.get('pages', []) or []) | set(ok_pages)
            (export_watermark_path(ed)).write_text(
                json.dumps({'at': utcnow(), 'pages': sorted(have)},
                           ensure_ascii=False, indent=1), encoding='utf-8')
        except (OSError, ValueError) as e:
            return {'ok': False,
                    'error': f'write failed (backup at {backup}): {e}'[:300],
                    'pages': {k: {kk: vv for kk, vv in v.items()
                                  if not kk.startswith('_')}
                              for k, v in built.items()},
                    'backup': backup}
    pages_out = {k: {kk: vv for kk, vv in v.items()
                     if not kk.startswith('_')}
                 for k, v in built.items()}
    return {'ok': True, 'edition': ed, 'backup': backup, 'pages': pages_out}


# ---------- any-box box->word anchors (Box N -> Word X, rest sequential) ----------
# editions/<ed>/anchors.json : {"<page>": [{"box": N, "key": "s:a:p"}], ...}.
# The reviewer anchors any box; following boxes continue sequentially from
# the anchor word. Unanchored leading boxes map from the page start.
def anchors_path(ed):
    return ROOT / 'editions' / ed / 'anchors.json'


def load_anchors(ed):
    try:
        d = json.loads(anchors_path(ed).read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def norm_page_anchors(v):
    """anchors.json page value -> sorted [{'box': int, 'key': str}]."""
    if not isinstance(v, list):
        return []
    out = []
    for a in v:
        if not isinstance(a, dict):
            continue
        try:
            b = int(a.get('box', 0))
        except (ValueError, TypeError):
            continue
        k = a.get('key')
        if not isinstance(k, str) or not KEY_RE.match(k) or b < 0:
            continue
        out.append({'box': b, 'key': k})
    out.sort(key=lambda a: a['box'])
    dedup = []
    for a in out:
        if dedup and dedup[-1]['box'] == a['box']:
            dedup[-1] = a
        else:
            dedup.append(a)
    return dedup


def write_anchors(ed, anchors):
    p = anchors_path(ed)
    tmp = p.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(anchors, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(p)


def check_anchor_key(ed, key):
    """Validate 's:a:p' against editions/<ed>/app_words.json. None = valid."""
    if not isinstance(key, str) or not KEY_RE.match(key):
        return 'key must look like "7:11:3"'
    try:
        app_words = json.loads((ROOT / 'editions' / ed / 'app_words.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        return f'unreadable app_words: {e}'
    s, a, p = (int(x) for x in key.split(':'))
    words = app_words.get(f'{s}:{a}')
    if not words:
        return f'ayah {s}:{a} has no words in this edition'
    if not 1 <= p <= len(words):
        return f'position {p} out of range for {s}:{a} (1..{len(words)})'
    return None


def page_word_span(ed, n):
    """Ordered [{key, arabic}] over the surahs touching page n (picker data).

    Wide enough to anchor Box 0 outside the expected span (drift pages);
    mirrors assign_words ordering (app-stream order, 's:a:p').
    Returns (words, first, last, skip, error)."""
    src = ROOT / 'editions' / ed
    try:
        app_words = json.loads((src / 'app_words.json').read_text(encoding='utf-8'))
        exp = json.loads((src / 'expectations.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        return None, None, None, None, f'unreadable edition data: {e}'
    P = exp.get('pages', {}).get(f'{n:03d}')
    if P is None:
        P = exp.get('pages', {}).get(str(n))
    if not P:
        return None, None, None, None, f'page {n} not in expectations'
    first = tuple(int(x) for x in P['first'].split(':'))
    last = tuple(int(x) for x in P['last'].split(':'))
    f_ay, l_ay = (first[0], first[1]), (last[0], last[1])
    words = []
    for sa in sorted(app_words, key=lambda k: tuple(int(x) for x in k.split(':'))):
        s, a = (int(x) for x in sa.split(':'))
        if not (f_ay <= (s, a) <= l_ay):
            continue
        for i, t in enumerate(app_words[sa], 1):
            words.append({'key': f'{s}:{a}:{i}', 'arabic': t})
    return words, P['first'], P['last'], sorted(P.get('skip', [])), None


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, PUT, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _save_tokens(self, ed, name, toks):
        backup_original(ed, name)
        edition_box_dir(ed).mkdir(parents=True, exist_ok=True)
        (edition_box_dir(ed) / name).write_text(
            json.dumps(toks, ensure_ascii=False, indent=2), encoding='utf-8')
        log_correction(ed, name, len(toks))
        print(f'saved editions/{ed}/boxes_raw/{name} ({len(toks)} tokens)', flush=True)

    def _read_save_body(self, max_len):
        try:
            length = int(self.headers.get('Content-Length', 0) or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > max_len:
            return None
        try:
            toks = json.loads(self.rfile.read(length))
            assert isinstance(toks, list)
            for t in toks:
                assert isinstance(t, dict) and isinstance(t.get('x0'), int)
        except Exception:
            return False
        return toks

    def do_GET(self):
        if self.path.split('?')[0].rstrip('/') in ('/api/status', '/api/status/'):
            return self._json(200, {'ok': True, 'finalize': True,
                                    'export': True,
                                    'version': SERVER_VERSION,
                                    'editions': list(EDITIONS)})
        parts = self.path.split('?')[0].strip('/').split('/')
        # GET /api/words/<ed>/<n>[.json] — picker data for start-anchored mapping
        # GET /api/anchor/<ed>/<n>[.json] — current Box-0 anchor for the page
        if len(parts) == 4 and parts[0] == 'api' and parts[1] in ('words', 'anchor') \
                and parts[2] in EDITIONS:
            name = (parts[3] or '')
            if name.endswith('.json'):
                name = name[:-5]
            if not name.isdigit():
                return self._json(404, {'ok': False, 'error': 'bad page'})
            n = int(name)
            if parts[1] == 'anchor':
                a = load_anchors(parts[2])
                pal = norm_page_anchors(a.get(str(n), a.get(f'{n:03d}')))
                return self._json(200, {'ok': True, 'page': n,
                                        'anchors': pal})
            words, first, last, skip, err = page_word_span(parts[2], n)
            if err:
                return self._json(404, {'ok': False, 'error': err})
            return self._json(200, {'ok': True, 'page': n, 'words': words,
                                    'first': first, 'last': last, 'skip': skip})
        return super().do_GET()

    def do_PUT(self):
        parts = self.path.split('?')[0].strip('/').split('/')
        # PUT /api/anchor/<ed>/<n>[.json]
        #   {"box": N, "key": "s:a:p"} — anchor Box N (upsert)
        #   {"box": N, "key": null}    — drop Box N's anchor
        #   {"clear": true}             — drop all page anchors
        if len(parts) == 4 and parts[0] == 'api' and parts[1] == 'anchor' \
                and parts[2] in EDITIONS:
            name = (parts[3] or '')
            if name.endswith('.json'):
                name = name[:-5]
            if not name.isdigit():
                return self._json(404, {'ok': False, 'error': 'bad page'})
            n = int(name)
            try:
                length = int(self.headers.get('Content-Length', 0) or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 100_000:
                return self._json(413, {'ok': False, 'error': 'bad length'})
            try:
                req = json.loads(self.rfile.read(length))
            except Exception:
                return self._json(400, {'ok': False, 'error': 'bad body'})
            if not isinstance(req, dict):
                return self._json(400, {'ok': False, 'error': 'bad body'})
            anchors = load_anchors(parts[2])
            cur = norm_page_anchors(anchors.get(str(n)))
            if req.get('clear'):
                cur = []
            elif 'box' in req or 'key' in req:
                try:
                    b = int(req.get('box', 0))
                except (ValueError, TypeError):
                    return self._json(400, {'ok': False, 'error': 'box must be a non-negative int'})
                if b < 0:
                    return self._json(400, {'ok': False, 'error': 'box must be a non-negative int'})
                key = req.get('key')
                cur = [a for a in cur if a['box'] != b]
                if key is not None:
                    err = check_anchor_key(parts[2], key)
                    if err:
                        return self._json(400, {'ok': False, 'error': err})
                    cur.append({'box': b, 'key': key})
                    cur.sort(key=lambda a: a['box'])
            else:
                return self._json(400, {'ok': False, 'error': 'bad body'})
            if cur:
                anchors[str(n)] = cur
            else:
                anchors.pop(str(n), None)
            try:
                write_anchors(parts[2], anchors)
            except OSError as e:
                return self._json(500, {'ok': False, 'error': str(e)[:200]})
            print(f'anchor {parts[2]} p{n} -> {cur}', flush=True)
            return self._json(200, {'ok': True, 'page': n, 'anchors': cur})
        if len(parts) == 4 and parts[0] == 'api' and parts[1] == 'save' \
                and parts[2] in EDITIONS and NAME_RE.match(parts[3] or ''):
            ed, name = parts[2], parts[3]
        else:
            return self._json(404, {'ok': False, 'error': 'bad path'})
        toks = self._read_save_body(MAX_BODY)
        if toks is None:
            return self._json(413, {'ok': False, 'error': 'bad length'})
        if toks is False:
            return self._json(400, {'ok': False, 'error': 'bad body'})
        try:
            self._save_tokens(ed, name, toks)
        except Exception as e:  # noqa: BLE001 -- report, never 500 silently
            print(f'save FAILED: {e}', flush=True)
            return self._json(500, {'ok': False, 'error': str(e)[:300]})
        return self._json(200, {'ok': True, 'saved': name, 'edition': ed})

    def do_POST(self):
        path = self.path.split('?')[0].rstrip('/')
        if path == '/api/export':
            try:
                length = int(self.headers.get('Content-Length', 0) or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 100_000:
                return self._json(413, {'ok': False, 'error': 'bad length'})
            try:
                req = json.loads(self.rfile.read(length))
                ed = req.get('edition', 'warsh')
                assert ed in EDITIONS
                pages = sorted(set(int(x) for x in req.get('pages', [])))
                assert pages and all(1 <= n <= 9999 for n in pages)
            except Exception:
                return self._json(400, {'ok': False, 'error': 'bad body'})
            try:
                return self._json(200, export_pages_to_app(ed, pages))
            except Exception as e:  # noqa: BLE001 -- report, never 500 silently
                print(f'export FAILED: {e}', flush=True)
                return self._json(500, {'ok': False, 'error': str(e)[:300]})
        if path != '/api/finalize':
            return self._json(404, {'ok': False, 'error': 'bad path'})
        try:
            length = int(self.headers.get('Content-Length', 0) or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 100_000:
            return self._json(413, {'ok': False, 'error': 'bad length'})
        try:
            req = json.loads(self.rfile.read(length))
            ed = req.get('edition', 'warsh')
            assert ed in EDITIONS
            pages = sorted(set(int(x) for x in req.get('pages', [])))
            assert pages and all(1 <= n <= 9999 for n in pages)
        except Exception:
            return self._json(400, {'ok': False, 'error': 'bad body'})
        try:
            return self._json(200, finalize_pages(ed, pages))
        except Exception as e:  # noqa: BLE001 -- report, never 500 silently
            print(f'finalize FAILED: {e}', flush=True)
            return self._json(500, {'ok': False, 'error': str(e)[:300]})

    def log_message(self, format, *args):  # quieter request log
        sys.stdout.write(f'{self.address_string()} {format % args}\n')


if __name__ == '__main__':
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    print(f'mushaf server on http://127.0.0.1:{PORT}/web/mushaf.html')
    print('Save button in Review mode writes editions/<edition>/boxes_raw/ with zero dialogs.')
    print('Ctrl+C to stop.')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
