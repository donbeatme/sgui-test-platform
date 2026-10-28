# Vendored OmniParser utilities

Source: https://github.com/microsoft/OmniParser/tree/3540212
Local upstream checkout commit: 3540212 (2026-07-19).
`util/` was already present in the local perception package. The standard
`run_ocr.py` pipeline uses RapidOCR and Ultralytics directly. These utility
files are retained for the existing experimental code; their `LICENSE` is
copied without alteration from the local upstream checkout.

Weights are downloaded separately using `../download_models.py` and verified
against `../models.json`. Individual model licenses are documented in the
upstream model card; the current icon_detect weights use AGPL-3.0.
