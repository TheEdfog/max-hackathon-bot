# Upstream

- Source: https://gitverse.ru/stasnorman/example-data-api
- Commit: `8504a6d9b39e6f652bce689d96e88538d7541c6b` (retrieved 2026-09-22).
- Author: Станислав Макиевский / Stanislav Makievskiy.
- Copyright © 2026 Stanislav Makievskiy.
- `validate_data_api.py` and `DATA-API.schema.json` are copied without logic/schema changes; only line endings and final blank lines may differ.
- Upstream `Example/DATA-API.schema.json` is placed next to the validator to support its default lookup.
- Upstream author's licence notice is preserved in `licence.md`. It refers to a `LICENSE` file that is not present in the retrieved repository; we do not invent a license identifier.
- `DATA-API.yaml` in the project root is our filled configuration based on the upstream template, not the author's example.
- Validator dependencies are pinned separately in `requirements-data-api.lock`.
