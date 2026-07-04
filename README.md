# GeoSplit

GeoSplit safely splits a GeoJSON `FeatureCollection` by feature count or exact file size. It can also convert GeoJSON to and from GeoPackage.

The splitter streams large inputs, preserves coordinate precision, limits memory to the active output chunk, checks available disk space, and uses recoverable output transactions.

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
```

If `geosplit` is not on your PATH, replace it with `python -m geosplit` or, on Windows, `py -m geosplit`.

## Split GeoJSON

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

### Preview with dry-run

Show planned files, feature counts, sizes, warnings, and conflicts without creating anything:

```bash
geosplit split world.geojson --features 1000 --dry-run
```

Dry-run still reads and validates the complete input.

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

Output files are numbered automatically, for example `world_001.geojson`. GeoSplit preserves top-level metadata except `bbox`, which would no longer describe each split collection. Interrupted transactions are recovered on the next run.

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
