# Transformer-only validation

Validated on 2026-09-28 in `C:\Users\zhuya\Code\data_transformer` with Python
3.12.10 and pytest 8.4.2 using the project's `.venv`.

- Editable installation refreshed successfully.
- Python compilation passed for `data_transformer`, `scripts`, and `tests`.
- **333 tests passed in 5.18 seconds.**
- Statement coverage: **99.53%** (1892/1901).
- Branch coverage: **99.00%** (796/804).
- Combined coverage: **99.37%**.

The project contains only the transformer implementation. Test discovery, package
metadata, coverage configuration, and the coverage runner reflect this scope.
No production lines or branches are excluded. Statement and branch coverage each
must reach 96% independently.

Run from the project folder:

```powershell
.\.venv\Scripts\python.exe scripts/run_coverage.py
```

Generated reports: `htmlcov_integration/index.html`, `coverage_integration.json`,
and `test_results_integration.xml`. Project-file hashes are in `COPY_MANIFEST.json`.
Existing transformer behavior and known limitations in `INTEGRATION_NOTES.md` remain.
