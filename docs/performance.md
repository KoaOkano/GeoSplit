# Performance policy

GeoSplit is expected to stream GeoJSON splitting and validation without loading a complete `FeatureCollection` into memory.

## What is measured

The benchmark runner records:

- processing time
- peak Python memory tracked by `tracemalloc`
- peak bytes observed under the output directory
- final output bytes
- output file count

Run the small benchmark locally:

```bash
python benchmarks/geojson_benchmark.py --features 2000 --operation all --json
```

Use `--work-dir` if the system temp directory is not writable:

```bash
python benchmarks/geojson_benchmark.py --work-dir .benchmark-work --operation all --json
```

Use different geometry mixes:

```bash
python benchmarks/geojson_benchmark.py --geometry point
python benchmarks/geojson_benchmark.py --geometry line
python benchmarks/geojson_benchmark.py --geometry polygon
python benchmarks/geojson_benchmark.py --geometry mixed
```

## CI and large runs

Regular CI runs a small benchmark on Windows and Linux. Large 1 GB, 5 GB, and 10 GB runs should be run manually or on a scheduled machine with enough disk space:

```bash
python benchmarks/geojson_benchmark.py --features 1000000 --geometry mixed --operation all --json
```

Choose the feature count that produces the desired source size on the target machine.

## Regression policy

Publish baseline results with each hardening release. Investigate changes greater than 10% in processing time, peak memory, or temporary disk usage.

Expected memory overhead for GeoJSON split and validate should remain bounded by parser state plus the active output chunk. GeoPackage operations may load a layer through GeoPandas and are measured separately from the streaming GeoJSON policy.
