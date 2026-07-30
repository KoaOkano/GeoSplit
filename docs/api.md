# Python API

GeoSplit 0.7 defines a small supported Python API. These names and their documented behavior are kept backward compatible within the 0.7 release series. A future breaking change will be announced in the changelog and released under a new version.

## Core imports

```python
from geosplit import (
    GeoSplitError,
    SplitPlan,
    SplitResult,
    ValidationReport,
    iter_batches,
    parse_size,
    plan_split,
    split_geojson,
    validate_geojson,
)
```

`GeoSplitError` is raised when input, options, or an operation is invalid. It subclasses `ValueError`.

### Stream batches

```python
for batch in iter_batches("input.geojson", features=1000):
    print(len(batch["features"]))
```

`iter_batches(source, *, features=None, max_bytes=None)` yields validated GeoJSON FeatureCollections without writing files. Supply exactly one of `features` or `max_bytes`. It streams the input and keeps only the active batch in memory.

### Parse a size

```python
size = parse_size("2.5MiB")
```

`parse_size(value)` returns the size in bytes. It accepts `B`, `KB`, `KiB`, `MB`, `MiB`, `GB`, and `GiB`; units are case-insensitive.

### Plan a split

```python
plan = plan_split("input.geojson", features_per_file=1000)
```

`plan_split(source, output_dir=None, *, features_per_file=None, max_bytes=None, prefix=None)` returns a `SplitPlan` without writing output.

A `SplitPlan` provides:

- `source` and `output_dir`
- `files`, including each planned path, feature count, and size
- `feature_count` and `total_bytes`
- `conflicts` and `warnings`

### Split GeoJSON

```python
result = split_geojson(
    "input.geojson",
    "output",
    features_per_file=1000,
    force=False,
)
```

`split_geojson(source, output_dir=None, *, features_per_file=None, max_bytes=None, prefix=None, force=False, progress=None)` writes the chunks and returns a `SplitResult`.

A `SplitResult` provides `files`, `feature_count`, and `total_bytes`. It can also be iterated or indexed as a sequence of output paths.

### Validate GeoJSON

```python
report = validate_geojson("input.geojson")
if not report.valid:
    print(report.errors)
```

`validate_geojson(source, progress=None)` returns a `ValidationReport` and does not write files. The report includes feature and geometry counts, null geometry count, maximum nesting, coordinate dimensions, warnings, and errors. `report.to_dict()` returns a JSON-serializable dictionary.

## GeoPackage and conversion imports

Install the optional GeoPackage dependencies first:

```bash
python -m pip install "geosplit[gpkg]"
```

```python
from geosplit.convert import (
    convert_file,
    plan_geopackage_split,
    split_geopackage,
)
```

`convert_file(source, destination, *, layer=None, output_layer=None, force=False, progress=None)` converts GeoJSON to GeoPackage or one GeoPackage layer to GeoJSON.

`plan_geopackage_split(source, output_dir=None, *, features_per_file=None, max_bytes=None, prefix=None, layer=None)` returns a `SplitPlan` without writing files.

`split_geopackage(source, output_dir=None, *, features_per_file=None, max_bytes=None, prefix=None, layer=None, force=False, progress=None)` splits one layer and keeps its layer name in every output file.

GeoPackage splitting supports feature-count mode only. Size-based splitting remains unsupported.

## Compatibility

The names listed on this page are the supported API. Modules, functions, and classes whose names begin with an underscore are internal and may change without notice.
