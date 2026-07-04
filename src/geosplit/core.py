"""Streaming GeoJSON splitting, planning, and validation."""

from __future__ import annotations

import errno
import math
import re
import shutil
from codecs import BOM_UTF8
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, BinaryIO, overload

import ijson
import simplejson as json

JsonObject = dict[str, Any]
ProgressCallback = Callable[[int, int], None]
_MAX_DEPTH = 100
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


class GeoSplitError(ValueError):
    """Raised when an input or requested operation is invalid."""


@dataclass(frozen=True)
class PlannedFile:
    """One file in a split plan."""

    path: Path
    feature_count: int
    size: int


@dataclass(frozen=True)
class SplitPlan:
    """A complete, non-writing split plan."""

    source: Path
    output_dir: Path
    files: tuple[PlannedFile, ...]
    feature_count: int
    total_bytes: int
    conflicts: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SplitResult(Sequence[Path]):
    """Summary of a completed split."""

    files: tuple[Path, ...]
    feature_count: int
    total_bytes: int

    @overload
    def __getitem__(self, index: int) -> Path: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[Path, ...]: ...

    def __getitem__(self, index: int | slice) -> Path | tuple[Path, ...]:
        return self.files[index]

    def __len__(self) -> int:
        return len(self.files)


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


def _open(path: Path) -> BinaryIO:
    source = path.open("rb")
    if source.read(3) != BOM_UTF8:
        source.seek(0)
    return source


def _check_depth(depth: int) -> None:
    if depth > _MAX_DEPTH:
        raise GeoSplitError(f"GeoJSON nesting exceeds the {_MAX_DEPTH}-level safety limit.")


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
                        _check_depth(depth)
                        depth -= event in {"end_map", "end_array"}
                        if not depth:
                            break
                    continue
                builder = ijson.ObjectBuilder()
                builder.event(event, value)
                depth = int(event in {"start_map", "start_array"})
                while depth:
                    _, event, value = next(events)
                    depth += event in {"start_map", "start_array"}
                    _check_depth(depth)
                    builder.event(event, value)
                    depth -= event in {"end_map", "end_array"}
                metadata[key] = builder.value
            if next(events, None) is not None:
                raise GeoSplitError("Input contains data after the GeoJSON object.")
    except GeoSplitError:
        raise
    except (OSError, UnicodeError, ijson.JSONError, StopIteration, RecursionError) as error:
        raise GeoSplitError(f"Cannot read valid GeoJSON from {path}: {error}") from error
    if metadata.get("type") != "FeatureCollection" or not has_features:
        raise GeoSplitError("Input must be a GeoJSON FeatureCollection.")
    metadata.pop("bbox", None)
    return metadata


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    return value.is_finite() if isinstance(value, Decimal) else math.isfinite(value)


def _position(value: Any) -> bool:
    return isinstance(value, list) and len(value) >= 2 and all(_is_number(number) for number in value)


def _line(value: Any, minimum: int = 2) -> bool:
    return isinstance(value, list) and len(value) >= minimum and all(_position(position) for position in value)


def _ring(value: Any) -> bool:
    return _line(value, 4) and value[0] == value[-1]


def _validate_geometry(geometry: Any, index: int, allow_null: bool = True) -> None:
    if geometry is None and allow_null:
        return
    if not isinstance(geometry, dict):
        raise GeoSplitError(f"Feature {index} has invalid geometry.")
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    valid = {
        "Point": lambda: _position(coordinates),
        "MultiPoint": lambda: isinstance(coordinates, list) and all(_position(item) for item in coordinates),
        "LineString": lambda: _line(coordinates),
        "MultiLineString": lambda: isinstance(coordinates, list) and all(_line(item) for item in coordinates),
        "Polygon": lambda: isinstance(coordinates, list) and all(_ring(item) for item in coordinates),
        "MultiPolygon": lambda: (
            isinstance(coordinates, list)
            and all(isinstance(polygon, list) and all(_ring(ring) for ring in polygon) for polygon in coordinates)
        ),
    }
    if geometry_type == "GeometryCollection":
        geometries = geometry.get("geometries")
        if not isinstance(geometries, list):
            raise GeoSplitError(f"Feature {index} has invalid geometry.")
        for child in geometries:
            _validate_geometry(child, index, allow_null=False)
    elif geometry_type in valid and coordinates == []:
        return
    elif geometry_type not in valid or not valid[geometry_type]():
        raise GeoSplitError(f"Feature {index} has invalid geometry or non-numeric coordinates.")


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
    except (OSError, UnicodeError, ijson.JSONError, RecursionError) as error:
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


def _validate_mode(features: int | None, max_bytes: int | None) -> None:
    if (features is None) == (max_bytes is None):
        raise GeoSplitError("Choose exactly one split mode: feature count or file size.")
    if features is not None and features < 1:
        raise GeoSplitError("Features per file must be at least 1.")
    if max_bytes is not None and max_bytes < 1:
        raise GeoSplitError("Maximum file size must be at least 1 byte.")


def _collections(
    metadata: JsonObject,
    feature_stream: Iterator[Any],
    features: int | None,
    max_bytes: int | None,
) -> Iterator[JsonObject]:
    chunks = (
        _chunks_by_count(feature_stream, features)
        if features is not None
        else _chunks_by_size(metadata, feature_stream, max_bytes)  # type: ignore[arg-type]
    )
    yielded = False
    try:
        for chunk in chunks:
            yielded = True
            yield {**metadata, "features": chunk}
        if not yielded:
            yield {**metadata, "features": []}
    finally:
        chunks.close()  # type: ignore[attr-defined]
        feature_stream.close()


def iter_batches(
    source: str | Path, *, features: int | None = None, max_bytes: int | None = None
) -> Iterator[JsonObject]:
    """Yield validated FeatureCollections without writing files."""
    _validate_mode(features, max_bytes)
    path = Path(source)
    yield from _collections(_metadata(path), _features(path), features, max_bytes)


def _disk_preflight(source: Path, destination: Path) -> None:
    required = max(source.stat().st_size * 5 // 4, 1_048_576)
    volume = destination
    while not volume.exists() and volume != volume.parent:
        volume = volume.parent
    available = shutil.disk_usage(volume).free
    if available < required:
        raise GeoSplitError(
            f"Insufficient disk space. Required: approximately {required:,} bytes; available: {available:,} bytes."
        )


def _safe_stem(source: Path, prefix: str | None) -> str:
    stem = prefix or source.stem
    if not stem or Path(stem).name != stem or stem in {".", ".."}:
        raise GeoSplitError("Prefix must be a filename, not a path.")
    return stem


def _output_dir(source: Path, output_dir: str | Path | None) -> Path:
    return Path(output_dir) if output_dir is not None else source.with_name(f"{source.stem}_split")


def _legacy_outputs(output_dir: Path, stem: str) -> list[Path]:
    if not output_dir.is_dir():
        return []
    pattern = re.compile(rf"{re.escape(stem)}_(\d{{3,}})\.geojson")
    indexed = sorted(
        (int(match.group(1)), path)
        for path in output_dir.iterdir()
        if path.is_file() and (match := pattern.fullmatch(path.name))
    )
    contiguous: list[Path] = []
    for expected, (index, path) in enumerate(indexed, 1):
        if index != expected:
            break
        contiguous.append(path)
    return contiguous


def _previous_outputs(output_dir: Path, stem: str) -> tuple[Path, list[Path], bool]:
    manifest = output_dir / f".{stem}.geosplit.json"
    if not manifest.exists():
        legacy = _legacy_outputs(output_dir, stem)
        return manifest, legacy, bool(legacy)
    try:
        names = json.loads(manifest.read_text(encoding="utf-8"))["files"]
        pattern = re.compile(rf"{re.escape(stem)}_\d+\.geojson")
        if not isinstance(names, list) or not all(isinstance(name, str) and pattern.fullmatch(name) for name in names):
            raise ValueError("invalid file list")
    except OSError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise GeoSplitError(f"Invalid GeoSplit manifest: {manifest}") from error
    return manifest, [output_dir / name for name in names], False


def _transaction_path(output_dir: Path, stem: str) -> Path:
    return output_dir / f".{stem}.geosplit-transaction"


def _operation_error(action: str, path: Path, error: OSError) -> GeoSplitError:
    if error.errno == errno.ENOSPC or getattr(error, "winerror", None) == 112:
        return GeoSplitError(f"Disk is full while trying to {action}: {path}")
    if isinstance(error, PermissionError) or getattr(error, "winerror", None) in {5, 32, 33}:
        return GeoSplitError(f"Permission denied or file is locked while trying to {action}: {path}")
    return GeoSplitError(f"Cannot {action} {path}: {error}")


def _journal(tx: Path, status: str, existing: list[Path], targets: list[Path]) -> None:
    data = {"status": status, "existing": [path.name for path in existing], "targets": [path.name for path in targets]}
    temporary = tx / "journal.tmp"
    temporary.write_bytes(_compact(data) + b"\n")
    temporary.replace(tx / "journal.json")


def _recover_transaction(output_dir: Path, stem: str) -> None:
    tx = _transaction_path(output_dir, stem)
    if not tx.exists():
        return
    journal = tx / "journal.json"
    try:
        if not journal.exists():
            shutil.rmtree(tx)
            return
        data = json.loads(journal.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid transaction journal")
        if data.get("status") == "complete":
            shutil.rmtree(tx)
            return
        pattern = re.compile(rf"(?:{re.escape(stem)}_\d+\.geojson|\.{re.escape(stem)}\.geosplit\.json)")
        existing, targets = data["existing"], data["targets"]
        if not all(isinstance(name, str) and pattern.fullmatch(name) for name in existing + targets):
            raise ValueError("invalid transaction paths")
        backups = tx / "backups"
        backup_names = {path.name for path in backups.iterdir()} if backups.exists() else set()
        for name in set(targets) - set(existing):
            (output_dir / name).unlink(missing_ok=True)
        for name in backup_names:
            target = output_dir / name
            target.unlink(missing_ok=True)
            (backups / name).replace(target)
        shutil.rmtree(tx)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, OSError):
            raise _operation_error("recover an interrupted transaction", tx, error) from error
        raise GeoSplitError(f"Cannot safely recover interrupted transaction: {tx}") from error


def _make_plan(
    source: Path,
    destination: Path,
    stem: str,
    summaries: list[tuple[int, int]],
    *,
    check_transaction: bool = True,
) -> SplitPlan:
    width = max(3, len(str(len(summaries))))
    files = tuple(
        PlannedFile(destination / f"{stem}_{index:0{width}d}.geojson", count, size)
        for index, (count, size) in enumerate(summaries, 1)
    )
    try:
        manifest, previous, legacy = _previous_outputs(destination, stem)
    except OSError as error:
        raise _operation_error("inspect output directory", destination, error) from error
    paths = [item.path for item in files]
    if source.resolve() in {path.resolve() for path in paths + previous}:
        raise GeoSplitError("Splitting would overwrite or remove the input.")
    conflicts = sorted({path for path in paths + previous if path.exists()})
    if manifest.exists():
        conflicts.append(manifest)
    warnings = []
    if legacy:
        warnings.append("Pre-manifest GeoSplit outputs were detected and will be adopted with --force.")
    stale = {path for path in previous if path.exists()} - set(paths)
    if stale:
        warnings.append(f"{len(stale)} stale output file(s) will be removed with --force.")
    if check_transaction and _transaction_path(destination, stem).exists():
        warnings.append("An interrupted transaction was detected; a real split will recover it first.")
    return SplitPlan(
        source,
        destination,
        files,
        sum(item.feature_count for item in files),
        sum(item.size for item in files),
        tuple(conflicts),
        tuple(warnings),
    )


def plan_split(
    source: str | Path,
    output_dir: str | Path | None = None,
    *,
    features_per_file: int | None = None,
    max_bytes: int | None = None,
    prefix: str | None = None,
) -> SplitPlan:
    """Return a complete split plan without changing the filesystem."""
    _validate_mode(features_per_file, max_bytes)
    source_path = Path(source)
    destination = _output_dir(source_path, output_dir)
    if destination.exists() and not destination.is_dir():
        raise GeoSplitError(f"Output path is not a directory: {destination}")
    stem = _safe_stem(source_path, prefix)
    summaries: list[tuple[int, int]] = []
    for batch in iter_batches(source_path, features=features_per_file, max_bytes=max_bytes):
        summaries.append((len(batch["features"]), len(_compact(batch)) + 1))
    return _make_plan(source_path, destination, stem, summaries)


def _commit(plan: SplitPlan, stem: str, force: bool, tx: Path) -> tuple[Path, ...]:
    manifest, previous, _ = _previous_outputs(plan.output_dir, stem)
    paths = [item.path for item in plan.files]
    existing = sorted({path for path in paths + previous if path.exists()})
    if not force and (conflict := manifest if manifest.exists() else next(iter(existing), None)):
        raise GeoSplitError(f"Output already exists: {conflict}. Use --force to replace it.")
    managed = [*existing, manifest] if manifest.exists() else existing
    targets = [*paths, manifest]
    backups = tx / "backups"
    try:
        _journal(tx, "prepared", managed, targets)
        backups.mkdir()
        for path in managed if force else []:
            path.replace(backups / path.name)
        for path in paths:
            (tx / "new" / path.name).replace(path)
        (tx / "new" / manifest.name).replace(manifest)
        _journal(tx, "complete", managed, targets)
    except OSError as error:
        try:
            _recover_transaction(plan.output_dir, stem)
        except GeoSplitError as recovery_error:
            raise GeoSplitError(
                f"{_operation_error('commit split files', plan.output_dir, error)}; {recovery_error}"
            ) from error
        raise _operation_error("commit split files", plan.output_dir, error) from error
    try:
        shutil.rmtree(tx)
    except OSError:
        pass
    return tuple(paths)


def split_geojson(
    source: str | Path,
    output_dir: str | Path | None = None,
    *,
    features_per_file: int | None = None,
    max_bytes: int | None = None,
    prefix: str | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> SplitResult:
    """Split a FeatureCollection and return a result summary."""
    _validate_mode(features_per_file, max_bytes)
    source_path = Path(source)
    destination = _output_dir(source_path, output_dir)
    stem = _safe_stem(source_path, prefix)
    if destination.exists() and not destination.is_dir():
        raise GeoSplitError(f"Output path is not a directory: {destination}")
    if destination.exists():
        _recover_transaction(destination, stem)
    try:
        source_state = source_path.stat()
    except OSError as error:
        raise _operation_error("read input", source_path, error) from error
    try:
        _disk_preflight(source_path, destination)
    except OSError as error:
        raise _operation_error("check available disk space on", destination, error) from error
    tx = _transaction_path(destination, stem)
    try:
        destination.mkdir(parents=True, exist_ok=True)
        tx.mkdir()
        staged = tx / "new"
        staged.mkdir()
        metadata = _metadata(source_path)
        batches = _collections(metadata, _features(source_path), features_per_file, max_bytes)
        summaries: list[tuple[int, int]] = []
        temporary: list[Path] = []
        processed = 0
        try:
            for created, batch in enumerate(batches, 1):
                data = _compact(batch) + b"\n"
                path = staged / f"{created:08d}.part"
                path.write_bytes(data)
                temporary.append(path)
                count = len(batch["features"])
                summaries.append((count, len(data)))
                processed += count
                if progress:
                    progress(processed, created)
        finally:
            batches.close()
        current_state = source_path.stat()
        if (source_state.st_size, source_state.st_mtime_ns) != (current_state.st_size, current_state.st_mtime_ns):
            raise GeoSplitError("Input changed while the split was being prepared; run the command again.")
        plan = _make_plan(source_path, destination, stem, summaries, check_transaction=False)
        if plan.conflicts and not force:
            raise GeoSplitError(f"Output already exists: {plan.conflicts[0]}. Use --force to replace it.")
        for source_file, item in zip(temporary, plan.files, strict=True):
            source_file.replace(staged / item.path.name)
        manifest = destination / f".{stem}.geosplit.json"
        (staged / manifest.name).write_bytes(
            _compact({"version": 1, "files": [item.path.name for item in plan.files]}) + b"\n"
        )
        files = _commit(plan, stem, force, tx)
    except GeoSplitError:
        raise
    except OSError as error:
        raise _operation_error("write split files", destination, error) from error
    finally:
        if tx.exists() and not (tx / "journal.json").exists():
            shutil.rmtree(tx, ignore_errors=True)
    return SplitResult(files, plan.feature_count, plan.total_bytes)
