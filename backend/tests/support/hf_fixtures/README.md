# Recorded Hugging Face answers (001 FTASKS 1.2, 4.1)

Captured 2026-10-06 from the public Hub API and Dataset Viewer with no token (public reads):

| File | Request | Status |
|---|---|---|
| `colbert_revision_short.json` | `GET /api/datasets/CreativeLang/ColBERT_Humor_Detection/revision/2bb7d6bc` | 200; sha `2bb7d6bce15e42c2a3cf2be8305fa3049929d3ac`, license `cc-by-2.0` |
| `colbert_head.json` | `GET /api/datasets/CreativeLang/ColBERT_Humor_Detection` | 200 |
| `colbert_splits.json`, `colbert_size.json`, `colbert_first_rows.json` | Dataset Viewer `/splits`, `/size`, `/first-rows` | 200 |
| `colbert_size_config.json` | Viewer `/size?config=default` (the shape changes: `size.config`, not `size.dataset`) | 200 |
| `gated_dataset.json` | `GET /api/datasets/lmsys/lmsys-chat-1m`, trimmed to the fields read | 200; `gated: auto` (metadata is public) |
| `gated_viewer_401.json` | Viewer `/first-rows` for that gated dataset, no token | 401 |
| `humicroedit_revision.json` | `.../tasksource/humicroedit/revision/f5a16e65…` | 200; license `unknown` |
| `humicroedit_revision_blobs.json` | `.../tasksource/humicroedit/revision/f5a16e65…?blobs=true` (file sizes: the import's size fallback when the Viewer has none) | 200 |
| `humicroedit_splits.json` | Viewer `/splits` for Humicroedit | 200; two configs (multi-config fixture) |
| `offensive_revision.json` | `.../tasksource/offensive-humor/revision/04b8f88d…` | 200; no card licence |
| `missing_revision.json` | `.../ColBERT_Humor_Detection/revision/no-such-ref-zz` | 404 |
| `not_found.json` | `GET /api/datasets/nobody-zz/no-such-dataset-zz` | 401 with no token (HF answers 401, not 404, for a repo it will not show) |

**Dated reading (001 FTASKS 1.2), 2026-10-06 16:47 UTC:** `GET /api/datasets/CreativeLang/ColBERT_Humor_Detection/revision/2bb7d6bc` → 200, sha `2bb7d6bce15e42c2a3cf2be8305fa3049929d3ac`; Viewer `/splits`, `/size`, `/first-rows` → 200 each, `application/json`. An invalid token sent to the Hub API for a PUBLIC dataset still answers 200, so a rejected token is detectable only where access is needed.

Synthetic cases (gated 403 with a token whose owner has not accepted the terms, a 200 HTML page, timeouts, a head commit different from the pinned
one) are built in `tests/support/hf_mock.py` from these shapes.
