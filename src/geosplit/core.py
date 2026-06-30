"""Core GeoJSON splitting logic."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

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


class GeoSplitError(ValueError):
    """Raised when an input or requested operation is invalid."""


def parse_size(value: str) -> int:
    """Convert values such as ``500KB`` or ``2.5MiB`` to bytes."""
    if not (match := _SIZE.fullmatch(value.strip())):
        raise GeoSplitError(f"Invalid size {value!r}; try 500KB, 2MB, or 1GiB.")
    amount, unit = match.groups()
    size = int(float(amount) * _UNITS[unit.upper() if unit else "B"])
    if size < 1:
        raise GeoSplitError("Size must be at least 1 byte.")
    return size


def _compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _load(path: Path) -> JsonObject:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError) as error:
        raise GeoSplitError(f"Cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise GeoSplitError(f"Invalid JSON in {path}: {error}") from error
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise GeoSplitError("Input must be a GeoJSON FeatureCollection.")
    if not isinstance(document.get("features"), list):
        raise GeoSplitError("GeoJSON 'features' must be an array.")
    return document


def _chunks_by_size(metadata: JsonObject, features: list[Any], limit: int) -> list[list[Any]]:
    # A trailing newline is included because output files contain one.
    fixed = len(_compact({**metadata, "features": []})) + 1
    if fixed > limit:
        raise GeoSplitError(f"The GeoJSON metadata alone exceeds the {limit}-byte limit.")

    chunks: list[list[Any]] = [[]]
    used = fixed
    for index, feature in enumerate(features, 1):
        feature_size = len(_compact(feature))
        addition = feature_size + bool(chunks[-1])
        if fixed + feature_size > limit:
            raise GeoSplitError(f"Feature {index} cannot fit within the {limit}-byte limit.")
        if used + addition > limit:
            chunks.append([])
            used = fixed
            addition -= 1
        chunks[-1].append(feature)
        used += addition
    return chunks


def split_geojson(
    source: str | Path,
    output_dir: str | Path,
    *,
    features_per_file: int | None = None,
    max_bytes: int | None = None,
    prefix: str | None = None,
    force: bool = False,
) -> list[Path]:
    """Split a FeatureCollection and return the output paths.

    Exactly one of ``features_per_file`` and ``max_bytes`` must be supplied.
    Size limits include the complete compact GeoJSON document and final newline.
    """
    if (features_per_file is None) == (max_bytes is None):
        raise GeoSplitError("Choose exactly one split mode: feature count or file size.")
    if features_per_file is not None and features_per_file < 1:
        raise GeoSplitError("Features per file must be at least 1.")
    if max_bytes is not None and max_bytes < 1:
        raise GeoSplitError("Maximum file size must be at least 1 byte.")

    source, output_dir = Path(source), Path(output_dir)
    document = _load(source)
    features = document.pop("features")
    # A collection-wide bbox becomes incorrect after splitting, so omit it.
    document.pop("bbox", None)
    chunks = (
        [features[i : i + features_per_file] for i in range(0, len(features), features_per_file)] or [[]]
        if features_per_file is not None
        else _chunks_by_size(document, features, max_bytes)  # type: ignore[arg-type]
    )
    stem, width = prefix or source.stem, max(3, len(str(len(chunks))))
    paths = [output_dir / f"{stem}_{i:0{width}d}.geojson" for i in range(1, len(chunks) + 1)]
    if not force and (existing := next((path for path in paths if path.exists()), None)):
        raise GeoSplitError(f"Output already exists: {existing}. Use --force to replace it.")

    output_dir.mkdir(parents=True, exist_ok=True)
    for path, chunk in zip(paths, chunks, strict=True):
        path.write_bytes(_compact({**document, "features": chunk}) + b"\n")
    return paths
