# GeoSplit
[![PyPI version](https://img.shields.io/pypi/v/geosplit.svg)](https://pypi.org/project/geosplit/)
[![Python versions](https://img.shields.io/pypi/pyversions/geosplit.svg)](https://pypi.org/project/geosplit/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

GeoSplit safely validates GeoJSON, splits a GeoJSON `FeatureCollection`, splits GeoPackage layers by feature count, and converts GeoJSON to and from GeoPackage.

The GeoJSON splitter streams large inputs, preserves coordinate precision, limits memory to the active output chunk, checks available disk space, and uses recoverable output transactions. GeoPackage support is optional.

## Install

GeoSplit requires Python 3.10 or newer:

```bash
python -m pip install geosplit
```

On Windows, `py` can be used instead:

```powershell
py -m pip install geosplit
```

Install optional GeoPackage support:

```bash
python -m pip install "geosplit[gpkg]"
```

Check the installation:

```bash
geosplit --version
geosplit --help
geosplit help split
geosplit help validate
```

If `geosplit` is not on your PATH, replace it with `python -m geosplit` or, on Windows, `py -m geosplit`.

## Validate GeoJSON

Validate a complete file without creating output:

```bash
geosplit validate input.geojson
```

The report includes feature and geometry counts, null geometries, maximum nesting, coordinate dimensions, and warnings. Invalid geometry errors identify the feature and coordinate path.

Produce a machine-readable report:

```bash
geosplit validate input.geojson --json
```

Validation checks JSON and GeoJSON structure, recognized geometry types, coordinate nesting, numeric finite coordinates, polygon ring length and closure, trailing data, and the nesting safety limit. It does not check geographic topology such as polygon self-intersections or silently repair data.

## Split GeoJSON and GeoPackage

Split every 1,000 features:

```bash
geosplit split world.geojson --features 1000
```

The output directory is optional. When omitted, GeoSplit creates `world_split` beside the input. To choose it explicitly:

```bash
geosplit split world.geojson output --features 1000
```

Split using an exact maximum output size:

```bash
geosplit split world.geojson output --size 10MB
```

Sizes accept `B`, `KB`, `KiB`, `MB`, `MiB`, `GB`, and `GiB`. Every output is a complete compact GeoJSON document. A feature that cannot fit by itself produces an error.

Split a GeoPackage layer by feature count:

```bash
geosplit split roads.gpkg --features 1000
```

If a GeoPackage contains multiple layers, choose one:

```bash
geosplit split map.gpkg --layer roads --features 1000
```

GeoPackage input creates GeoPackage output files and keeps the same layer name inside every chunk. Size-based splitting is not supported for GeoPackage input. Use `--features` instead.

### Preview with dry-run

Show planned files, feature counts, sizes, warnings, and conflicts without creating anything:

```bash
geosplit split world.geojson --features 1000 --dryrun
```

Dry-run still reads and validates the complete input. The older spelling `--dry-run` is still accepted.

### Other split options

Choose the output filename prefix:

```bash
geosplit split world.geojson --features 1000 --prefix countries
```

Replace output files previously managed by GeoSplit:

```bash
geosplit split world.geojson --features 1000 --force
```

Suppress progress and success output for scripts:

```bash
geosplit split world.geojson --features 1000 --quiet
```

Options can be combined:

```bash
geosplit split world.geojson output --size 50MiB --prefix region --force --quiet
```

Output files are numbered automatically, for example `world_001.geojson` or `roads_001.gpkg`. GeoSplit preserves top-level GeoJSON metadata except `bbox`, which would no longer describe each split collection. Interrupted transactions are recovered on the next run.

In an interactive terminal, long-running `split`, `validate`, and `convert` commands show progress, for example:

```text
Reading features     45,000 / 180,000
Writing chunks       12 / 48
Validating output    48 / 48
```

Use `--quiet` with `split` to suppress split progress and success output. Validation JSON output stays machine-readable and does not include progress text.

## Convert GeoJSON and GeoPackage

Install `geosplit[gpkg]` first, then run:

```bash
# GeoJSON to GeoPackage
geosplit convert roads.geojson roads.gpkg

# Select the new GeoPackage layer name
geosplit convert roads.geojson map.gpkg --output-layer roads

# GeoPackage to GeoJSON
geosplit convert map.gpkg roads.geojson --layer roads

# Replace an existing destination
geosplit convert roads.geojson roads.gpkg --force
```

If a GeoPackage contains exactly one layer, `--layer` is optional.

## Python API

Stream validated collections without writing files:

```python
from geosplit import iter_batches

for collection in iter_batches("world.geojson", features=1000):
    process(collection)
```

Plan without writing, then perform a split:

```python
from geosplit import plan_split, split_geojson

plan = plan_split("world.geojson", features_per_file=1000)
result = split_geojson("world.geojson", features_per_file=1000)

print(plan.files)
print(result.files)
print(result.feature_count)
print(result.total_bytes)
```

Use `max_bytes` instead of `features_per_file` for exact-size splitting.

Validate from Python:

```python
from geosplit import validate_geojson

report = validate_geojson("world.geojson")
print(report.valid)
print(report.feature_count)
print(report.geometry_counts)
print(report.errors)
```

## Safety behavior

- Existing output is protected unless `--force` is supplied.
- `--force` only replaces files tracked by GeoSplit or recognized legacy output.
- Output is staged before replacing existing files.
- Coordinate values retain their parsed decimal precision.
- Invalid geometry structure, non-finite coordinates, corrupt JSON, and excessive nesting are rejected.
- A disk-space estimate is checked before staging; operating-system write errors are still handled if free space changes later.

## Update

```bash
python -m pip install --upgrade geosplit
```

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

MIT
