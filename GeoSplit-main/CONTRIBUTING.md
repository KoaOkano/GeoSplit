# Contributing to GeoSplit

Keep changes focused on safe, predictable GeoJSON splitting. Prefer small standard-library solutions and avoid new dependencies unless they clearly reduce risk or complexity.

## Development

```bash
python -m venv .venv
python -m pip install -e ".[dev,gpkg]"
python -m pytest
python -m ruff check .
```

Add tests for behavior changes. Filesystem changes must preserve existing output when an operation fails. Public API additions require documentation and compatibility consideration.

Open a focused pull request describing the problem, the solution, and the checks you ran.
