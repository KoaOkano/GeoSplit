"""Core GeoJSON splitting logic."""

from __future__ import annotations

import re
from codecs import BOM_UTF8
from collections.abc import Iterable, Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import ijson
import simplejson as json

JsonObject = dict[str, Any]
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*(B|KB|KIB|MB|MIB|GB|GIB)?$", re.I)
_UNITS = {
    "B": 1,
    "KB": 1_000,
    "KIB": 1_024,
    "MB": 1_000_000,
    "MIB": 1_048_576,
    "GB": 1_000_000_000,
    "GIB": 1_073_741_824,
}
_GEOMETRIES = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}


class GeoSplitError(ValueError):
    """Raised when an input or requested operation is invalid."""


def parse_size(value: str) -> int:
    """Convert values such as ``500KB`` or ``2.5MiB`` to bytes."""
    if not (match := _SIZE.fullmatch(value.strip())):
        raise GeoSplitError(f"Invalid size {value!r}; try 500KB, 2MB, or 1GiB.")
    amount, unit = match.groups()
    try:
        size = int(Decimal(amount) * _UNITS[unit.upper() if unit else "B"])
    except (InvalidOperation, OverflowError) as error:
        raise GeoSplitError(f"Invalid size {value!r}.") from error
    if size < 1:
        raise GeoSplitError("Size must be at least 1 byte.")
    return size


def _compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _open(path: Path):
    source = path.open("rb")
    if source.read(3) != BOM_UTF8:
        source.seek(0)
    return source


def _metadata(path: Path) -> JsonObject:
    metadata: JsonObject = {}
    has_features = False
    try:
        with _open(path) as source:
            events = iter(ijson.parse(source))
            if next(events)[1] != "start_map":
                raise GeoSplitError("Input must be a GeoJSON FeatureCollection.")
            for _, event, key in events:
                if event == "end_map":
                    break
                if event != "map_key":
                    raise GeoSplitError("Invalid GeoJSON object.")
                _, event, value = next(events)
                if key == "features":
                    if event != "start_array":
                        raise GeoSplitError("GeoJSON 'features' must be an array.")
                    has_features = True
                    depth = 1
                    for _, event, _ in events:
                        depth += event in {"start_map", "start_array"}
                        depth -= event in {"end_map", "end_array"}
                        if not depth:
                            break
                    continue
                builder = ijson.ObjectBuilder()
                builder.event(event, value)
                depth = int(event in {"start_map", "start_array"})
                while depth:
                    _, event, value = next(events)
                    builder.event(event, value)
                    depth += event in {"start_map", "start_array"}
                    depth -= event in {"end_map", "end_array"}
                metadata[key] = builder.value
            if next(events, None) is not None:
                raise GeoSplitError("Input contains data after the GeoJSON object.")
    except GeoSplitError:
        raise
    except (OSError, UnicodeError, ijson.JSONError, StopIteration) as error:
        raise GeoSplitError(f"Cannot read valid GeoJSON from {path}: {error}") from error
    if metadata.get("type") != "FeatureCollection" or not has_features:
        raise GeoSplitError("Input must be a GeoJSON FeatureCollection.")
    metadata.pop("bbox", None)
    return metadata


def _validate_geometry(geometry: Any, index: int, allow_null: bool = True) -> None:
    if geometry is None and allow_null:
        return
    if not isinstance(geometry, dict) or geometry.get("type") not in _GEOMETRIES | {"GeometryCollection"}:
        raise GeoSplitError(f"Feature {index} has invalid geometry.")
    member = "geometries" if geometry["type"] == "GeometryCollection" else "coordinates"
    if member not in geometry or not isinstance(geometry[member], list):
        raise GeoSplitError(f"Feature {index} has invalid geometry.")
    if member == "geometries":
        for child in geometry[member]:
            _validate_geometry(child, index, allow_null=False)


def _features(path: Path) -> Iterator[Any]:
    try:
        with _open(path) as source:
            for index, feature in enumerate(ijson.items(source, "features.item"), 1):
                if (
                    not isinstance(feature, dict)
                    or feature.get("type") != "Feature"
                    or "geometry" not in feature
                    or "properties" not in feature
                    or not isinstance(feature.get("properties"), (dict, type(None)))
                ):
                    raise GeoSplitError(f"Feature {index} is not a valid GeoJSON Feature.")
                _validate_geometry(feature["geometry"], index)
                yield feature
    except GeoSplitError:
        raise
    except (OSError, UnicodeError, ijson.JSONError) as error:
        raise GeoSplitError(f"Cannot read valid GeoJSON from {path}: {error}") from error


def _chunks_by_count(features: Iterable[Any], limit: int) -> Iterator[list[Any]]:
    chunk: list[Any] = []
    for feature in features:
        chunk.append(feature)
        if len(chunk) == limit:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _chunks_by_size(metadata: JsonObject, features: Iterable[Any], limit: int) -> Iterator[list[Any]]:
    fixed = len(_compact({**metadata, "features": []})) + 1
    if fixed > limit:
        raise GeoSplitError(f"The GeoJSON metadata alone exceeds the {limit}-byte limit.")
    chunk: list[Any] = []
    used = fixed
    for index, feature in enumerate(features, 1):
        feature_size = len(_compact(feature))
        if fixed + feature_size > limit:
            raise GeoSplitError(f"Feature {index} cannot fit within the {limit}-byte limit.")
        if used + feature_size + bool(chunk) > limit:
            yield chunk
            chunk, used = [], fixed
        chunk.append(feature)
        used += feature_size + (len(chunk) > 1)
    if chunk:
        yield chunk


def _safe_stem(source: Path, prefix: str | None) -> str:
    stem = prefix or source.stem
    if not stem or Path(stem).name != stem or stem in {".", ".."}:
        raise GeoSplitError("Prefix must be a filename, not a path.")
    return stem


def _previous_outputs(output_dir: Path, stem: str) -> tuple[Path, list[Path]]:
    manifest = output_dir / f".{stem}.geosplit.json"
    if not manifest.exists():
        return manifest, []
    try:
        names = json.loads(manifest.read_text(encoding="utf-8"))["files"]
        pattern = re.compile(rf"{re.escape(stem)}_\d+\.geojson")
        if not isinstance(names, list) or not all(isinstance(name, str) and pattern.fullmatch(name) for name in names):
            raise ValueError("invalid file list")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise GeoSplitError(f"Invalid GeoSplit manifest: {manifest}") from error
    return manifest, [output_dir / name for name in names]


def _commit(source: Path, output_dir: Path, stem: str, temporary: Path, count: int, force: bool) -> list[Path]:
    width = max(3, len(str(count)))
    paths = [output_dir / f"{stem}_{index:0{width}d}.geojson" for index in range(1, count + 1)]
    manifest, previous = _previous_outputs(output_dir, stem)
    existing = sorted({path for path in paths + previous if path.exists()})
    if source.resolve() in {path.resolve() for path in paths + previous}:
        raise GeoSplitError("Splitting would overwrite or remove the input.")
    if not force and (conflict := manifest if manifest.exists() else next(iter(existing), None)):
        raise GeoSplitError(f"Output already exists: {conflict}. Use --force to replace it.")

    backups = temporary / "backups"
    installed: list[Path] = []
    try:
        (temporary / "manifest").write_bytes(_compact({"files": [path.name for path in paths]}) + b"\n")
        backups.mkdir()
        managed = [*existing, manifest] if manifest.exists() else existing
        for path in managed if force else []:
            path.replace(backups / path.name)
        for index, path in enumerate(paths, 1):
            (temporary / str(index)).replace(path)
            installed.append(path)
        (temporary / "manifest").replace(manifest)
        installed.append(manifest)
    except OSError as error:
        for path in installed:
            path.unlink(missing_ok=True)
        if backups.exists():
            for backup in backups.iterdir():
                backup.replace(output_dir / backup.name)
        raise GeoSplitError(f"Cannot write split files: {error}") from error
    return paths


def split_geojson(
    source: str | Path,
    output_dir: str | Path,
    *,
    features_per_file: int | None = None,
    max_bytes: int | None = None,
    prefix: str | None = None,
    force: bool = False,
) -> list[Path]:
    """Split a FeatureCollection and return the output paths."""
    if (features_per_file is None) == (max_bytes is None):
        raise GeoSplitError("Choose exactly one split mode: feature count or file size.")
    if features_per_file is not None and features_per_file < 1:
        raise GeoSplitError("Features per file must be at least 1.")
    if max_bytes is not None and max_bytes < 1:
        raise GeoSplitError("Maximum file size must be at least 1 byte.")

    source, output_dir = Path(source), Path(output_dir)
    stem = _safe_stem(source, prefix)
    metadata = _metadata(source)
    chunks = (
        _chunks_by_count(_features(source), features_per_file)
        if features_per_file is not None
        else _chunks_by_size(metadata, _features(source), max_bytes)  # type: ignore[arg-type]
    )
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".geosplit-", dir=output_dir) as temporary_name:
            temporary = Path(temporary_name)
            count = 0
            for count, chunk in enumerate(chunks, 1):
                (temporary / str(count)).write_bytes(_compact({**metadata, "features": chunk}) + b"\n")
            if not count:
                count = 1
                (temporary / "1").write_bytes(_compact({**metadata, "features": []}) + b"\n")
            return _commit(source, output_dir, stem, temporary, count, force)
    except GeoSplitError:
        raise
    except OSError as error:
        raise GeoSplitError(f"Cannot write split files: {error}") from error
