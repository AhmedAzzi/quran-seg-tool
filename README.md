# quran-seg-tool

Local web QA tool for mushaf word-box segmentation: review every page of
each mushaf edition, correct word boxes visually, and export the
corrections. Covers **3 editions × 604 pages**: Warsh, Hafs, Asbahani.

## Run

```sh
py web/serve.py            # or double-click web/serve.bat
```

Open http://127.0.0.1:8765/web/mushaf.html
(or open `web/mushaf.html` directly for read-only review — saving needs
the server).

## How it works

The page shows one mushaf page with its word boxes overlaid. The single
**Save** button does save + finalize + export in one click via the
localhost-only server (`127.0.0.1`, 5 MB body cap):

- `PUT /api/save/<edition>/<n>.json` — edited boxes → `editions/<edition>/boxes_raw/`
- `POST /api/finalize` — rebuild the `web/` bundle slices
- `POST /api/export` — patch `editions/<edition>/page_boxes.json` +
  `page_markers.json` (the pack inputs; next push rebuilds the packs)

The reviewer's deliverable is `editions/<edition>/boxes_raw/` plus
`editions/<edition>/corrections_log.jsonl` (one line per applied save).

## Layout

- `web/mushaf.html` — the review app (works from `file://`, saving needs `serve.py`)
- `web/serve.py` / `web/serve.bat` — localhost companion server for one-click save
- `web/<edition>/` — per-edition web bundle: `review/` + `tokens/` slices
  (one file per page, `1.js` … `604.js`) plus `reader_data.js`
- `editions/<edition>/` — per-edition workspace: `boxes_raw/` (edited boxes),
  `boxes_final/`, `pages/` (page scans), oracle/numbering/expectation JSONs,
  `corrections_log.jsonl`
- `tool/build_pack.py` — assembles the downloadable `warsh.zip` / `hafs.zip` /
  `asbahani.zip` packs (packs-v2 layout) from `editions/` over a pinned base
  pack; run `python3 tool/build_pack.py <edition>`
- `.github/workflows/build-packs.yml` — rebuilds + republishes all three
  packs on the `packs` release on every push touching `editions/` or `web/`
