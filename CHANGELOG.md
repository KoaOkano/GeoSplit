# Changelog

## 0.3.1 - 2026-07-03

### Fixed

- Preserve coordinate precision while splitting GeoJSON.
- Limit `--force` cleanup to files recorded by GeoSplit.
- Reject trailing data after a GeoJSON document.

### Changed

- Require GeoPandas 1.1.2 or newer for GeoPackage support.

### Tests

- Add precision, Unicode-path, corrupt-file, large-file, and safer-force coverage.
