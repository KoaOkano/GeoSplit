"""Streaming GeoJSON splitting, planning, and validation."""

from __future__ import annotations

import errno
import math
import re
import shutil
from codecs import BOM_UTF8
from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, BinaryIO, overload

import ijson
import simplejson as json

JsonObject = dict[str, Any]
_JsonEvent = tuple[str, str, Any]
ProgressCallback = Callable[[int, int], None]
_GeometryValidator = Callable[[Any, int], None]
_BatchSummary = tuple[int, int]
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


@dataclass(frozen=True)
class _SplitPaths:
    source: Path
    output_dir: Path
    stem: str


@dataclass(frozen=True)
class _OutputState:
    manifest: Path
    files: tuple[Path, ...]
    legacy: bool


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


def _skip_container(events: Iterator[_JsonEvent]) -> None:
    depth = 1
    for _, event, _ in events:
        depth += event in {"start_map", "start_array"}
        _check_depth(depth)
        depth -= event in {"end_map", "end_array"}
        if not depth:
            return


def _build_value(events: Iterator[_JsonEvent], event: str, value: Any) -> Any:
    builder = ijson.ObjectBuilder()
    builder.event(event, value)
    depth = int(event in {"start_map", "start_array"})
    while depth:
        _, event, value = next(events)
        depth += event in {"start_map", "start_array"}
        _check_depth(depth)
        builder.event(event, value)
        depth -= event in {"end_map", "end_array"}
    return builder.value


def _read_metadata(path: Path, *, keep_bbox: bool = False) -> JsonObject:
    """Read top-level metadata while validating the complete JSON document."""
    metadata: JsonObject = {}
    has_features = False
    try:
        with _open(path) as source:
            events: Iterator[_JsonEvent] = iter(ijson.parse(source))
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
                    _skip_container(events)
                    continue
                metadata[key] = _build_value(events, event, value)
            if next(events, None) is not None:
                raise GeoSplitError("Input contains data after the GeoJSON object.")
    except (OSError, UnicodeError, ijson.JSONError, StopIteration, RecursionError) as error:
        raise GeoSplitError(f"Cannot read valid GeoJSON from {path}: {error}") from error
    if metadata.get("type") != "FeatureCollection" or not has_features:
        raise GeoSplitError("Input must be a GeoJSON FeatureCollection.")
    if not keep_bbox:
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


def _points(value: Any) -> bool:
    return isinstance(value, list) and all(_position(item) for item in value)


def _lines(value: Any) -> bool:
    return isinstance(value, list) and all(_line(item) for item in value)


def _polygon(value: Any) -> bool:
    return isinstance(value, list) and all(_ring(item) for item in value)


def _polygons(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(polygon, list) and all(_ring(ring) for ring in polygon) for polygon in value
    )


_COORDINATE_VALIDATORS: dict[str, Callable[[Any], bool]] = {
    "Point": _position,
    "MultiPoint": _points,
    "LineString": _line,
    "MultiLineString": _lines,
    "Polygon": _polygon,
    "MultiPolygon": _polygons,
}


def _validate_geometry(geometry: Any, index: int, allow_null: bool = True, depth: int = 1) -> None:
    _check_depth(depth)
    if geometry is None and allow_null:
        return
    if not isinstance(geometry, dict):
        raise GeoSplitError(f"Feature {index} has invalid geometry.")
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "GeometryCollection":
        geometries = geometry.get("geometries")
        if not isinstance(geometries, list):
            raise GeoSplitError(f"Feature {index} has invalid geometry.")
        for child in geometries:
            _validate_geometry(child, index, allow_null=False, depth=depth + 1)
    elif geometry_type in _COORDINATE_VALIDATORS and coordinates == []:
        return
    elif geometry_type not in _COORDINATE_VALIDATORS or not _COORDINATE_VALIDATORS[geometry_type](coordinates):
        raise GeoSplitError(f"Feature {index} has invalid geometry or non-numeric coordinates.")


def _validate_feature(feature: Any, index: int) -> JsonObject:
    if (
        not isinstance(feature, dict)
        or feature.get("type") != "Feature"
        or "geometry" not in feature
        or "properties" not in feature
        or not isinstance(feature.get("properties"), (dict, type(None)))
    ):
        raise GeoSplitError(f"Feature {index} is not a valid GeoJSON Feature.")
    return feature


def _iter_features(
    path: Path, geometry_validator: _GeometryValidator | None = None
) -> Generator[JsonObject, None, None]:
    """Stream and validate each feature in source order."""
    validator = geometry_validator or _validate_geometry
    try:
        with _open(path) as source:
            for index, feature in enumerate(ijson.items(source, "features.item"), 1):
                feature = _validate_feature(feature, index)
                validator(feature["geometry"], index)
                yield feature
    except (OSError, UnicodeError, ijson.JSONError, RecursionError) as error:
        raise GeoSplitError(f"Cannot read valid GeoJSON from {path}: {error}") from error


def _chunks_by_count(features: Iterable[JsonObject], limit: int) -> Generator[list[JsonObject], None, None]:
    chunk: list[JsonObject] = []
    for feature in features:
        chunk.append(feature)
        if len(chunk) == limit:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _chunks_by_size(
    metadata: JsonObject, features: Iterable[JsonObject], limit: int
) -> Generator[list[JsonObject], None, None]:
    fixed = len(_compact({**metadata, "features": []})) + 1
    if fixed > limit:
        raise GeoSplitError(f"The GeoJSON metadata alone exceeds the {limit}-byte limit.")
    chunk: list[JsonObject] = []
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


def _iter_collections(
    metadata: JsonObject,
    feature_stream: Generator[JsonObject, None, None],
    features: int | None,
    max_bytes: int | None,
) -> Generator[JsonObject, None, None]:
    if features is not None:
        chunks = _chunks_by_count(feature_stream, features)
    else:
        assert max_bytes is not None
        chunks = _chunks_by_size(metadata, feature_stream, max_bytes)
    yielded = False
    try:
        for chunk in chunks:
            yielded = True
            yield {**metadata, "features": chunk}
        if not yielded:
            yield {**metadata, "features": []}
    finally:
        chunks.close()
        feature_stream.close()


def iter_batches(
    source: str | Path, *, features: int | None = None, max_bytes: int | None = None
) -> Iterator[JsonObject]:
    """Yield validated FeatureCollections without writing files."""
    _validate_mode(features, max_bytes)
    path = Path(source)
    yield from _iter_collections(_read_metadata(path), _iter_features(path), features, max_bytes)


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


def _split_paths(source: str | Path, output_dir: str | Path | None, prefix: str | None) -> _SplitPaths:
    source_path = Path(source)
    destination = _output_dir(source_path, output_dir)
    if destination.exists() and not destination.is_dir():
        raise GeoSplitError(f"Output path is not a directory: {destination}")
    return _SplitPaths(source_path, destination, _safe_stem(source_path, prefix))


def _legacy_outputs(output_dir: Path, stem: str, suffix: str = ".geojson") -> list[Path]:
    if not output_dir.is_dir():
        return []
    pattern = re.compile(rf"{re.escape(stem)}_(\d{{3,}}){re.escape(suffix)}")
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


def _load_output_state(output_dir: Path, stem: str, suffix: str = ".geojson") -> _OutputState:
    """Load managed output, or discover contiguous output from pre-manifest releases."""
    manifest = output_dir / f".{stem}.geosplit.json"
    if not manifest.exists():
        legacy = _legacy_outputs(output_dir, stem, suffix)
        return _OutputState(manifest, tuple(legacy), bool(legacy))
    try:
        names = json.loads(manifest.read_text(encoding="utf-8"))["files"]
        pattern = re.compile(rf"{re.escape(stem)}_\d+{re.escape(suffix)}")
        if not isinstance(names, list) or not all(isinstance(name, str) and pattern.fullmatch(name) for name in names):
            raise ValueError("invalid file list")
    except OSError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise GeoSplitError(f"Invalid GeoSplit manifest: {manifest}") from error
    return _OutputState(manifest, tuple(output_dir / name for name in names), False)


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


def _recover_transaction(output_dir: Path, stem: str, suffix: str = ".geojson") -> None:
    """Restore old output or discard a completed/interrupted transaction."""
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
        pattern = re.compile(
            rf"(?:{re.escape(stem)}_\d+{re.escape(suffix)}|\.{re.escape(stem)}\.geosplit\.json)"
        )
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


def _build_plan(
    source: Path,
    destination: Path,
    stem: str,
    summaries: list[_BatchSummary],
    *,
    suffix: str = ".geojson",
    check_transaction: bool = True,
) -> SplitPlan:
    """Build names, conflicts, and warnings from already-computed batch summaries."""
    width = max(3, len(str(len(summaries))))
    files = tuple(
        PlannedFile(destination / f"{stem}_{index:0{width}d}{suffix}", count, size)
        for index, (count, size) in enumerate(summaries, 1)
    )
    try:
        output_state = _load_output_state(destination, stem, suffix)
    except OSError as error:
        raise _operation_error("inspect output directory", destination, error) from error
    paths = [item.path for item in files]
    if source.resolve() in {path.resolve() for path in [*paths, *output_state.files]}:
        raise GeoSplitError("Splitting would overwrite or remove the input.")
    conflicts = sorted({path for path in [*paths, *output_state.files] if path.exists()})
    if output_state.manifest.exists():
        conflicts.append(output_state.manifest)
    warnings = []
    if output_state.legacy:
        warnings.append("Pre-manifest GeoSplit outputs were detected and will be adopted with --force.")
    stale = {path for path in output_state.files if path.exists()} - set(paths)
    if stale:
        warnings.append(f"{len(stale)} stale output file(s) will be removed with --force.")
    if check_transaction and _transaction_path(destination, stem).exists():
        warnings.append("An interrupted transaction was detected; a real split will recover it first.")
    return SplitPlan(
        source,
        destination,
        files,
        sum(item.feature_count for item in files),
        sum(item.size for item in files) if all(item.size >= 0 for item in files) else -1,
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
    paths = _split_paths(source, output_dir, prefix)
    summaries: list[_BatchSummary] = []
    for batch in iter_batches(paths.source, features=features_per_file, max_bytes=max_bytes):
        summaries.append((len(batch["features"]), len(_compact(batch)) + 1))
    return _build_plan(paths.source, paths.output_dir, paths.stem, summaries)


def _commit_transaction(
    plan: SplitPlan, stem: str, force: bool, tx: Path, suffix: str = ".geojson"
) -> tuple[Path, ...]:
    """Atomically replace managed output using the prepared transaction."""
    output_state = _load_output_state(plan.output_dir, stem, suffix)
    paths = [item.path for item in plan.files]
    existing = sorted({path for path in [*paths, *output_state.files] if path.exists()})
    if not force and (
        conflict := output_state.manifest if output_state.manifest.exists() else next(iter(existing), None)
    ):
        raise GeoSplitError(f"Output already exists: {conflict}. Use --force to replace it.")
    managed = [*existing, output_state.manifest] if output_state.manifest.exists() else existing
    targets = [*paths, output_state.manifest]
    backups = tx / "backups"
    try:
        _journal(tx, "prepared", managed, targets)
        backups.mkdir()
        for path in managed if force else []:
            path.replace(backups / path.name)
        for path in paths:
            (tx / "new" / path.name).replace(path)
        (tx / "new" / output_state.manifest.name).replace(output_state.manifest)
        _journal(tx, "complete", managed, targets)
    except OSError as error:
        try:
            _recover_transaction(plan.output_dir, stem, suffix)
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


def _stage_batches(
    staged: Path,
    batches: Generator[JsonObject, None, None],
    progress: ProgressCallback | None,
) -> tuple[list[_BatchSummary], list[Path]]:
    """Write numbered temporary batches and return their counts and sizes."""
    summaries: list[_BatchSummary] = []
    temporary_files: list[Path] = []
    processed = 0
    try:
        for index, batch in enumerate(batches, 1):
            data = _compact(batch) + b"\n"
            temporary = staged / f"{index:08d}.part"
            temporary.write_bytes(data)
            temporary_files.append(temporary)
            feature_count = len(batch["features"])
            summaries.append((feature_count, len(data)))
            processed += feature_count
            if progress:
                progress(processed, index)
    finally:
        batches.close()
    return summaries, temporary_files


def _finalize_staged_files(staged: Path, temporary_files: list[Path], plan: SplitPlan, stem: str) -> None:
    """Apply final names and add the manifest inside the staging directory."""
    for temporary, item in zip(temporary_files, plan.files, strict=True):
        temporary.replace(staged / item.path.name)
    manifest = staged / f".{stem}.geosplit.json"
    manifest.write_bytes(_compact({"version": 1, "files": [item.path.name for item in plan.files]}) + b"\n")


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
    paths = _split_paths(source, output_dir, prefix)
    source_path, destination, stem = paths.source, paths.output_dir, paths.stem
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
        metadata = _read_metadata(source_path)
        batches = _iter_collections(metadata, _iter_features(source_path), features_per_file, max_bytes)
        summaries, temporary_files = _stage_batches(staged, batches, progress)
        current_state = source_path.stat()
        if (source_state.st_size, source_state.st_mtime_ns) != (current_state.st_size, current_state.st_mtime_ns):
            raise GeoSplitError("Input changed while the split was being prepared; run the command again.")
        plan = _build_plan(source_path, destination, stem, summaries, check_transaction=False)
        if plan.conflicts and not force:
            raise GeoSplitError(f"Output already exists: {plan.conflicts[0]}. Use --force to replace it.")
        _finalize_staged_files(staged, temporary_files, plan, stem)
        files = _commit_transaction(plan, stem, force, tx)
    except OSError as error:
        raise _operation_error("write split files", destination, error) from error
    finally:
        if tx.exists() and not (tx / "journal.json").exists():
            shutil.rmtree(tx, ignore_errors=True)
    return SplitResult(files, plan.feature_count, plan.total_bytes)
