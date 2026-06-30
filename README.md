# GeoSplit

A focused command-line tool that:

- splits a GeoJSON `FeatureCollection` by feature count;
- splits it by a strict maximum file size; and
- converts GeoJSON to GeoPackage, or a GeoPackage layer back to GeoJSON.

The splitter has no runtime dependencies. GeoPackage support is optional.

View general or command-specific help at any time:

```bash
geosplit help
geosplit help split
geosplit help convert
```

## Install

Requires Python 3.10 or newer. Download or clone the project, open a terminal in its folder, then run:

```bash
pip install .
```

Include conversion support:

```bash
pip install ".[gpkg]"
```

## Split GeoJSON

By feature count:

```bash
geosplit split world.geojson output --features 1000
```

By maximum file size:

```bash
geosplit split world.geojson output --size 10MB
```

Sizes accept `B`, `KB`, `KiB`, `MB`, `MiB`, `GB`, and `GiB`. Each resulting file is a complete, compact GeoJSON document whose on-disk size does not exceed the requested limit. If one feature cannot fit by itself, the command stops with a clear error.

Use `--prefix countries` to customize output names or `--force` to replace matching files. A source collection's top-level `bbox` is omitted because it would no longer describe each split collection; other top-level metadata is retained.

## Convert formats

```bash
# GeoJSON to GeoPackage
geosplit convert roads.geojson roads.gpkg

# Choose the new GeoPackage layer name
geosplit convert roads.geojson map.gpkg --output-layer roads

# GeoPackage to GeoJSON
geosplit convert map.gpkg roads.geojson --layer roads
```

If a GeoPackage contains exactly one layer, `--layer` is optional. Existing destinations are protected unless `--force` is supplied.

## License

MIT
