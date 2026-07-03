# Changelog

## 0.4.1 - 2026-07-04

### Release

- Add PyPI Trusted Publishing through GitHub Actions.
- Verify tests, linting, package metadata, and the release tag before publishing.

## 0.4.0 - 2026-07-04

### Added

- Add non-writing split plans and the `--dry-run` option.
- Add automatic `<input-name>_split` output directories. Output directories no longer needs to be specified.
- Add public `iter_batches()` and `plan_split()` APIs.
- Add `SplitResult` summaries with files, feature counts, and byte totals.
- Add CLI progress, `--quiet`, and `--version`.

### Improved

- Validate coordinate nesting and numeric values for every GeoJSON geometry type.
- Reject JSON nesting deeper than 100 levels.
- Recover interrupted output and manifest transactions.
- Adopt contiguous outputs created before manifests were introduced.
- Improve full-disk, permission, and locked-file errors.

### Tests

- Add dry-run, default-output, generator cleanup, geometry, malicious-input, recovery, legacy-output,
  large-file memory, and Windows locked-file coverage.

## 0.3.1 - 2026-07-03

### Fixed

- Preserve coordinate precision while splitting GeoJSON.
- Limit `--force` cleanup to files recorded by GeoSplit.
- Reject trailing data after a GeoJSON document.

### Changed

- Require GeoPandas 1.1.2 or newer for GeoPackage support.

### Tests

- Add precision, Unicode-path, corrupt-file, large-file, and safer-force coverage.
