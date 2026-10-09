Shared contracts owned by miDataworks (ADR-017). This directory ships in the public mirror, so each
file's `$id` resolves.

| File | Contract | Consumers | Source and writer |
|---|---|---|---|
| `midataworks-dataset-version-v1.json` | `midataworks.dataset-version/v1`, the handoff manifest written beside every published or exported version (feature 008) | miStudio (034 FR-24–26), miForge (BRD-02 R-02.37); both vendor it byte-identical | generated from `backend/src/schemas/dataset_version.py` by `cd backend && python -m scripts.write_dataset_version_schema`, never edited by hand; pinned by `backend/tests/unit/contract/test_dataset_version_schema_sync.py` |
| `hf-repo-id-cases.json` | the Hugging Face repository ID rule's shared test cases (feature 001) | the backend and frontend tests | hand-written |
