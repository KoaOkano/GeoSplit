"""Streaming GeoJSON validation and reporting."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

from .core import GeoSplitError, _is_number, _iter_features, _read_metadata

_ItemValidator = Callable[[Any, int, str], None]


@dataclass(frozen=True)
class ValidationReport:
    """Result of validating one GeoJSON FeatureCollection."""

    source: Path
    valid: bool
    feature_count: int
    geometry_counts: dict[str, int]
    null_geometry_count: int
    maximum_nesting: int
    coordinate_dimensions: tuple[int, ...]
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "valid": self.valid,
            "source": str(self.source),
            "feature_count": self.feature_count,
            "geometry_types": self.geometry_counts,
            "null_geometries": self.null_geometry_count,
            "maximum_nesting": self.maximum_nesting,
            "coordinate_dimensions": list(self.coordinate_dimensions),
            "coordinate_precision": "preserved",
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def _error(feature: int, path: str, message: str) -> NoReturn:
    raise GeoSplitError(f"Feature {feature} at {path}: {message}")


def _position(value: Any, feature: int, path: str) -> None:
    if not isinstance(value, list) or len(value) < 2:
        _error(feature, path, "position must contain at least two coordinates.")
    for index, coordinate in enumerate(value):
        if not _is_number(coordinate):
            _error(feature, f"{path}[{index}]", "coordinate must be numeric and finite.")


def _line(value: Any, feature: int, path: str, minimum: int = 2) -> None:
    if not isinstance(value, list) or len(value) < minimum:
        _error(feature, path, f"must contain at least {minimum} positions.")
    for index, position in enumerate(value):
        _position(position, feature, f"{path}[{index}]")


def _ring(value: Any, feature: int, path: str) -> None:
    _line(value, feature, path, 4)
    if value[0] != value[-1]:
        _error(feature, path, "polygon ring must be closed.")


def _require_array(value: Any, feature: int, path: str) -> list[Any]:
    if not isinstance(value, list):
        _error(feature, path, "must be an array.")
    return value


def _validate_items(value: Any, feature: int, path: str, validator: _ItemValidator) -> None:
    for index, item in enumerate(_require_array(value, feature, path)):
        validator(item, feature, f"{path}[{index}]")


def _positions(value: Any, feature: int, path: str) -> None:
    _validate_items(value, feature, path, _position)


def _lines(value: Any, feature: int, path: str) -> None:
    _validate_items(value, feature, path, _line)


def _rings(value: Any, feature: int, path: str) -> None:
    _validate_items(value, feature, path, _ring)


def _polygons(value: Any, feature: int, path: str) -> None:
    _validate_items(value, feature, path, _rings)


_COORDINATE_VALIDATORS: dict[str, _ItemValidator] = {
    "Point": _position,
    "MultiPoint": _positions,
    "LineString": _line,
    "MultiLineString": _lines,
    "Polygon": _rings,
    "MultiPolygon": _polygons,
}


def _validate_geometry(geometry: Any, feature: int, path: str = "geometry", *, allow_null: bool = True) -> None:
    if geometry is None and allow_null:
        return
    if not isinstance(geometry, dict):
        _error(feature, path, "must be a GeoJSON geometry object or null.")

    geometry_type = geometry.get("type")
    if geometry_type == "GeometryCollection":
        geometries = _require_array(geometry.get("geometries"), feature, f"{path}.geometries")
        for index, child in enumerate(geometries):
            _validate_geometry(child, feature, f"{path}.geometries[{index}]", allow_null=False)
        return

    coordinates = geometry.get("coordinates")
    if geometry_type not in _COORDINATE_VALIDATORS:
        _error(feature, f"{path}.type", f"unrecognized geometry type {geometry_type!r}.")
    if coordinates == []:
        return
    _COORDINATE_VALIDATORS[geometry_type](coordinates, feature, f"{path}.coordinates")


def _nesting(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_nesting(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_nesting(item) for item in value), default=0)
    return 0


def _dimensions(value: Any) -> Iterator[int]:
    if isinstance(value, list) and len(value) >= 2 and all(_is_number(item) for item in value):
        yield len(value)
        return
    if isinstance(value, list):
        for item in value:
            yield from _dimensions(item)


def _geometry_dimensions(geometry: Any) -> Iterator[int]:
    if not isinstance(geometry, dict):
        return
    if geometry.get("type") == "GeometryCollection":
        for child in geometry.get("geometries", []):
            yield from _geometry_dimensions(child)
    else:
        yield from _dimensions(geometry.get("coordinates"))


def _warnings(dimensions: set[int]) -> tuple[str, ...]:
    warnings = []
    if len(dimensions) > 1:
        warnings.append(f"Mixed coordinate dimensions found: {', '.join(map(str, sorted(dimensions)))}.")
    if any(dimension > 3 for dimension in dimensions):
        warnings.append("Coordinate dimensions greater than 3 were found.")
    return tuple(warnings)


def validate_geojson(source: str | Path) -> ValidationReport:
    """Validate a GeoJSON FeatureCollection without writing to the filesystem."""
    path = Path(source)
    feature_count = null_geometry_count = maximum_nesting = 0
    geometry_counts: Counter[str] = Counter()
    dimensions: set[int] = set()
    feature_stream = None
    errors: tuple[str, ...] = ()
    try:
        metadata = _read_metadata(path, keep_bbox=True)
        maximum_nesting = max(2, *(1 + _nesting(value) for value in metadata.values()))
        feature_stream = _iter_features(path, _validate_geometry)
        for feature_count, feature in enumerate(feature_stream, 1):
            geometry = feature["geometry"]
            maximum_nesting = max(maximum_nesting, 2 + _nesting(feature))
            if geometry is None:
                null_geometry_count += 1
                continue
            geometry_counts[cast(str, geometry["type"])] += 1
            dimensions.update(_geometry_dimensions(geometry))
    except GeoSplitError as error:
        errors = (str(error),)
    finally:
        if feature_stream is not None:
            feature_stream.close()

    return ValidationReport(
        source=path,
        valid=not errors,
        feature_count=feature_count,
        geometry_counts=dict(sorted(geometry_counts.items())),
        null_geometry_count=null_geometry_count,
        maximum_nesting=maximum_nesting,
        coordinate_dimensions=tuple(sorted(dimensions)),
        warnings=_warnings(dimensions),
        errors=errors,
    )
