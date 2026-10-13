# Reproduction and publication verification

The public source export was prepared on 13 October 2026 in a new directory with a new Python virtual environment and a new Git repository. Original generated models, datasets and the old virtual environment were not copied into the export.

## Publication scope

Included: project-owned Python, dashboard HTML/CSS/JS, tests, configuration templates, Docker/Make files, three historical JSON result summaries, and reproduction documentation. The historical summaries contain aggregate metrics and artifact hashes; they are not new measurements.

Excluded: original raw/processed datasets, trained weights, course handouts, report/presentation drafts, media, submission archive, device metadata, terminal logs, private `.env`, caches, installed packages and unrelated projects. A fresh root commit is used; no parent repository history is imported. There was no existing source license to preserve or extend, and no new license was invented. Third-party dependencies are installed from package registries rather than vendored.

The source review checked staged filenames and text for private keys, common access-token formats, personal paths, email addresses, binary documents and excluded artifact types. Literal original development database credentials were replaced by local configuration placeholders. This is a scoped review, not a guarantee that a pattern scanner can detect every possible secret.

## Scoped reproducibility changes

- Replaced machine-specific instructions with a complete reproduction guide and macOS ARM64 dependency snapshot.
- Required local InfluxDB configuration, added configurable host ports and localhost-only published bindings for the exported demo.
- Added a Docker build-context allowlist and ignored local evidence/caches/package metadata.
- Made measurement output directories self-creating and CPU metadata collection portable beyond `sysctl`.
- Made host MQTT/dashboard evidence capture honor port settings.
- Required train/test/RUL files in the dataset completeness check, instead of accepting training files alone.
- Corrected displayed benchmark size units to KiB/MiB; retained historical JSON field names for compatibility.

## Verification status

Verified on macOS 26.5.1 / Apple M1 Pro / Python 3.11.13; uv 0.8.0, Docker Engine 29.8.2, Compose 5.5.1. A new virtual environment used the recorded ARM64 dependency snapshot. An initially stale editable install caused `ModuleNotFoundError`; reinstalling only the project with the README troubleshooting command restored it. No pre-existing generated model or virtual environment was used.

- `make test`: **6 passed**. `ruff check src tests scripts`: **all checks passed**. `uv pip check`: **60 packages compatible**.
- Fresh `make download`: NASA URL attempted, Zenodo fallback downloaded successfully; published MD5 matched and extraction succeeded.
- Default CPU threading was slow (two epochs in several minutes); that run was interrupted. `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONUNBUFFERED=1 make train` completed 11 epochs with early stopping. The resulting FP32, INT8 and metadata SHA256 hashes all match the retained historical hashes.
- `make benchmark` and `scripts/collect_measurements.py` completed. INT8 validation F1 **0.8717518860**, official test endpoint F1 **0.8**, all-window test F1 **0.7048611111**, threshold **0.64**.
- New single-run mean inference latency: FP32 **0.0596403 ms**, INT8 **0.1082172 ms**. These measurements were made on a busy development host and are functional reproduction evidence, not a controlled speed comparison or a promise of INT8 acceleration. See [fresh benchmark JSON](reproduction-results/benchmark.json).
- `docker compose config --quiet` and runtime image build succeeded. All seven services ran in an isolated Compose project using host ports 11883/18086/18010. Linux aarch64 edge runtime loaded ONNX Runtime **1.31.0** and the newly generated model.
- Health API returned `status=ok`, `mqtt_connected=true`; dashboard HTML/JavaScript returned successfully; snapshot contained live telemetry and predictions.
- Live capture recorded **447 MQTT messages**, **7 non-retained feedback/status events**, and persisted `telemetry`, `prediction`, `alert`, `status` measurements. Automatic alert → controller command → simulator acknowledgement and manual reset acknowledgement were asserted.
- The temporary stack was stopped after validation; its database volume is preserved. Existing unrelated stacks were not stopped or modified.

Browser automation was unavailable (neither in-app browser nor Chrome exposed by the tool), so no fresh visual/screenshot audit is claimed. Native Linux host training, Windows/WSL2, physical hardware and the final defense device remain untested. No CI workflow is included; verification here is local. Historical results remain separately labelled in `reference-results/`.
