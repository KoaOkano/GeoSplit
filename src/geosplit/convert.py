"""Optional GeoJSON and GeoPackage conversion support."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from .core import GeoSplitError

_GEOJSON = {".geojson", ".json"}


def convert_file(
    source: str | Path,
    destination: str | Path,
    *,
    layer: str | None = None,
    output_layer: str | None = None,
    force: bool = False,
) -> Path:
    """Convert one GeoJSON file to GeoPackage, or one GeoPackage layer to GeoJSON."""
    source_path = Path(source)
    destination_path = Path(destination)
    source_suffix = source_path.suffix.lower()
    destination_suffix = destination_path.suffix.lower()
    source_is_package = source_suffix == ".gpkg"
    destination_is_package = destination_suffix == ".gpkg"
    unsupported_suffixes = {source_suffix, destination_suffix} - (_GEOJSON | {".gpkg"})
    if source_is_package == destination_is_package or unsupported_suffixes:
        raise GeoSplitError("Conversion requires one .geojson/.json file and one .gpkg file.")
    if not source_path.is_file():
        raise GeoSplitError(f"Input does not exist or is not a file: {source_path}")
    if layer and not source_is_package:
        raise GeoSplitError("--layer only applies when reading a GeoPackage.")
    if output_layer and not destination_is_package:
        raise GeoSplitError("--output-layer only applies when writing a GeoPackage.")
    if destination_path.exists() and not force:
        raise GeoSplitError(f"Output already exists: {destination_path}. Use --force to replace it.")
    try:
        import geopandas as gpd
    except ImportError as error:
        raise GeoSplitError("GeoPackage support is not installed. Run: pip install 'geosplit[gpkg]'") from error

    try:
        if source_is_package:
            layers = gpd.list_layers(source_path)["name"].tolist()
            if not layers:
                raise GeoSplitError("GeoPackage contains no layers.")
            if layer is None and len(layers) != 1:
                raise GeoSplitError(f"GeoPackage has multiple layers; choose one with --layer: {', '.join(layers)}")
            if layer is not None and layer not in layers:
                raise GeoSplitError(f"Layer {layer!r} not found. Available layers: {', '.join(layers)}")
            frame, driver, options = gpd.read_file(source_path, layer=layer or layers[0]), "GeoJSON", {}
        else:
            frame = gpd.read_file(source_path)
            driver, options = "GPKG", {"layer": output_layer or destination_path.stem}

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".geosplit-", dir=destination_path.parent) as temporary_dir:
            temporary_output = Path(temporary_dir) / destination_path.name
            frame.to_file(temporary_output, driver=driver, index=False, **options)
            temporary_output.replace(destination_path)
    except GeoSplitError:
        raise
    except Exception as error:
        raise GeoSplitError(f"Conversion failed: {error}") from error
    return destination_path
