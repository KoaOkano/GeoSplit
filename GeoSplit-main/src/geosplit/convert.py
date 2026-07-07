"""Optional GeoJSON and GeoPackage support."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .core import (
    GeoSplitError,
    SplitPlan,
    SplitResult,
    _build_plan,
    _commit_transaction,
    _disk_preflight,
    _finalize_staged_files,
    _operation_error,
    _recover_transaction,
    _split_paths,
    _transaction_path,
    _validate_mode,
)

_GEOJSON = {".geojson", ".json"}
_GPKG = ".gpkg"
ProgressReporter = Callable[[str, int, int | None], None]


def _geopandas() -> Any:
    try:
        import geopandas as gpd
    except ImportError as error:
        raise GeoSplitError("GeoPackage support is not installed. Run: pip install 'geosplit[gpkg]'") from error
    return gpd


def _selected_layer(gpd: Any, source: Path, layer: str | None) -> str:
    layers = gpd.list_layers(source)["name"].tolist()
    if not layers:
        raise GeoSplitError("GeoPackage contains no layers.")
    if layer is None and len(layers) != 1:
        raise GeoSplitError(f"GeoPackage has multiple layers; choose one with --layer: {', '.join(layers)}")
    if layer is not None and layer not in layers:
        raise GeoSplitError(f"Layer {layer!r} not found. Available layers: {', '.join(layers)}")
    return layer or layers[0]


def _read_geopackage_layer(gpd: Any, source: Path, layer: str | None) -> tuple[Any, str]:
    selected = _selected_layer(gpd, source, layer)
    return gpd.read_file(source, layer=selected), selected


def convert_file(
    source: str | Path,
    destination: str | Path,
    *,
    layer: str | None = None,
    output_layer: str | None = None,
    force: bool = False,
    progress: ProgressReporter | None = None,
) -> Path:
    """Convert one GeoJSON file to GeoPackage, or one GeoPackage layer to GeoJSON."""
    source_path = Path(source)
    destination_path = Path(destination)
    source_suffix = source_path.suffix.lower()
    destination_suffix = destination_path.suffix.lower()
    source_is_package = source_suffix == _GPKG
    destination_is_package = destination_suffix == _GPKG
    unsupported_suffixes = {source_suffix, destination_suffix} - (_GEOJSON | {_GPKG})
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
    gpd = _geopandas()

    try:
        if source_is_package:
            frame, _ = _read_geopackage_layer(gpd, source_path, layer)
            driver, options = "GeoJSON", {}
        else:
            frame = gpd.read_file(source_path)
            driver, options = "GPKG", {"layer": output_layer or destination_path.stem}
        if progress:
            progress("Reading features", len(frame), len(frame))

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".geosplit-", dir=destination_path.parent) as temporary_dir:
            temporary_output = Path(temporary_dir) / destination_path.name
            frame.to_file(temporary_output, driver=driver, index=False, **options)
            if progress:
                progress("Writing output", 1, 1)
            temporary_output.replace(destination_path)
    except GeoSplitError:
        raise
    except Exception as error:
        raise GeoSplitError(f"Conversion failed: {error}") from error
    return destination_path


def _validate_geopackage_split_mode(features_per_file: int | None, max_bytes: int | None) -> None:
    if max_bytes is not None:
        raise GeoSplitError("Size-based splitting is not supported for GeoPackage input. Use --features instead.")
    _validate_mode(features_per_file, None)


def _require_geopackage(path: Path) -> None:
    if path.suffix.lower() != _GPKG:
        raise GeoSplitError("GeoPackage split input must be a .gpkg file.")
    if not path.is_file():
        raise GeoSplitError(f"Input does not exist or is not a file: {path}")


def _frame_chunks(frame: Any, features_per_file: int) -> Iterator[Any]:
    if len(frame) == 0:
        yield frame
        return
    for start in range(0, len(frame), features_per_file):
        yield frame.iloc[start : start + features_per_file]


def _geopackage_summaries(feature_count: int, features_per_file: int) -> list[tuple[int, int]]:
    if feature_count == 0:
        return [(0, -1)]
    return [
        (min(features_per_file, feature_count - start), -1)
        for start in range(0, feature_count, features_per_file)
    ]


def plan_geopackage_split(
    source: str | Path,
    output_dir: str | Path | None = None,
    *,
    features_per_file: int | None = None,
    max_bytes: int | None = None,
    prefix: str | None = None,
    layer: str | None = None,
) -> SplitPlan:
    """Return a GeoPackage split plan without writing output files."""
    _validate_geopackage_split_mode(features_per_file, max_bytes)
    assert features_per_file is not None
    paths = _split_paths(source, output_dir, prefix)
    _require_geopackage(paths.source)
    gpd = _geopandas()

    try:
        frame, _ = _read_geopackage_layer(gpd, paths.source, layer)
        plan = _build_plan(
            paths.source,
            paths.output_dir,
            paths.stem,
            _geopackage_summaries(len(frame), features_per_file),
            suffix=_GPKG,
        )
    except GeoSplitError:
        raise
    except Exception as error:
        raise GeoSplitError(f"Cannot plan GeoPackage split: {error}") from error
    return replace(plan, warnings=plan.warnings + ("GeoPackage output sizes are not estimated during dry-run.",))


def split_geopackage(
    source: str | Path,
    output_dir: str | Path | None = None,
    *,
    features_per_file: int | None = None,
    max_bytes: int | None = None,
    prefix: str | None = None,
    layer: str | None = None,
    force: bool = False,
    progress: ProgressReporter | None = None,
) -> SplitResult:
    """Split one GeoPackage layer by feature count."""
    _validate_geopackage_split_mode(features_per_file, max_bytes)
    assert features_per_file is not None
    paths = _split_paths(source, output_dir, prefix)
    source_path, destination, stem = paths.source, paths.output_dir, paths.stem
    _require_geopackage(source_path)
    if destination.exists():
        _recover_transaction(destination, stem, _GPKG)
    try:
        source_state = source_path.stat()
    except OSError as error:
        raise _operation_error("read input", source_path, error) from error
    try:
        _disk_preflight(source_path, destination)
    except OSError as error:
        raise _operation_error("check available disk space on", destination, error) from error

    gpd = _geopandas()
    try:
        frame, layer_name = _read_geopackage_layer(gpd, source_path, layer)
    except GeoSplitError:
        raise
    except Exception as error:
        raise GeoSplitError(f"GeoPackage split failed: {error}") from error
    if progress:
        progress("Reading features", len(frame), len(frame))

    tx = _transaction_path(destination, stem)
    try:
        destination.mkdir(parents=True, exist_ok=True)
        tx.mkdir()
        staged = tx / "new"
        staged.mkdir()
        total_chunks = max(1, (len(frame) + features_per_file - 1) // features_per_file)
        summaries: list[tuple[int, int]] = []
        temporary_files: list[Path] = []
        for index, chunk in enumerate(_frame_chunks(frame, features_per_file), 1):
            temporary = staged / f"{index:08d}.gpkg"
            chunk.to_file(temporary, driver="GPKG", layer=layer_name, index=False)
            temporary_files.append(temporary)
            summaries.append((len(chunk), temporary.stat().st_size))
            if progress:
                progress("Writing chunks", index, total_chunks)

        current_state = source_path.stat()
        if (source_state.st_size, source_state.st_mtime_ns) != (current_state.st_size, current_state.st_mtime_ns):
            raise GeoSplitError("Input changed while the split was being prepared; run the command again.")

        plan = _build_plan(source_path, destination, stem, summaries, suffix=_GPKG, check_transaction=False)
        if plan.conflicts and not force:
            raise GeoSplitError(f"Output already exists: {plan.conflicts[0]}. Use --force to replace it.")
        _finalize_staged_files(staged, temporary_files, plan, stem)
        files = _commit_transaction(plan, stem, force, tx, _GPKG)
        if progress:
            progress("Validating output", sum(path.exists() for path in files), len(files))
    except GeoSplitError:
        raise
    except OSError as error:
        raise _operation_error("write split files", destination, error) from error
    except Exception as error:
        raise GeoSplitError(f"GeoPackage split failed: {error}") from error
    finally:
        if tx.exists() and not (tx / "journal.json").exists():
            shutil.rmtree(tx, ignore_errors=True)
    return SplitResult(files, plan.feature_count, plan.total_bytes)
