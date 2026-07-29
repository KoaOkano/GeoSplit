import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from geosplit import validate_geojson
from geosplit.core import GeoSplitError, _recover_transaction, split_geojson


coordinates = st.tuples(
    st.floats(min_value=-180, max_value=180, allow_nan=False, allow_infinity=False),
    st.floats(min_value=-90, max_value=90, allow_nan=False, allow_infinity=False),
).map(list)


@st.composite
def geometries(draw):
    point = {"type": "Point", "coordinates": draw(coordinates)}
    line = {"type": "LineString", "coordinates": draw(st.lists(coordinates, min_size=2, max_size=5))}
    ring_points = draw(st.lists(coordinates, min_size=3, max_size=5))
    polygon = {"type": "Polygon", "coordinates": [[*ring_points, ring_points[0]]]}
    return draw(st.sampled_from([point, line, polygon, None]))


@st.composite
def feature_collections(draw):
    features = []
    for index, geometry in enumerate(draw(st.lists(geometries(), min_size=0, max_size=12))):
        features.append({"type": "Feature", "properties": {"id": index}, "geometry": geometry})
    return {"type": "FeatureCollection", "features": features}


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(document=feature_collections())
def test_generated_feature_collections_validate_and_split(document: dict[str, object], tmp_path: Path) -> None:
    source = _write_json(tmp_path / "input.geojson", document)
    report = validate_geojson(source)
    assert report.valid
    outputs = split_geojson(source, tmp_path / "out", features_per_file=3, force=True)
    assert sum(len(path.read_text(encoding="utf-8")) for path in outputs) > 0


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=st.binary(min_size=1, max_size=200))
def test_random_bytes_do_not_crash_validation(data: bytes, tmp_path: Path) -> None:
    source = tmp_path / "input.geojson"
    source.write_bytes(data)
    assert validate_geojson(source).valid in {True, False}


@pytest.mark.parametrize(
    "content",
    [
        b'{"type":"FeatureCollection","features":[{"type":"Feature"',
        b'{"type":"FeatureCollection","features":[]}\xff',
        b'{"type":"FeatureCollection","features":[{"type":"Feature","properties":{},"geometry":{"type":"Point",'
        b'"coordinates":[NaN,0]}}]}',
    ],
)
def test_malformed_input_is_reported(content: bytes, tmp_path: Path) -> None:
    source = tmp_path / "bad.geojson"
    source.write_bytes(content)
    assert not validate_geojson(source).valid


def test_huge_finite_number_does_not_crash_validation(tmp_path: Path) -> None:
    source = tmp_path / "huge.geojson"
    source.write_bytes(
        b'{"type":"FeatureCollection","features":[{"type":"Feature","properties":{},'
        b'"geometry":{"type":"Point","coordinates":[1e1000000,0]}}]}'
    )
    assert validate_geojson(source).valid in {True, False}


def test_deep_geometry_collection_is_rejected(tmp_path: Path) -> None:
    geometry: dict[str, object] = {"type": "Point", "coordinates": [0, 0]}
    for _ in range(110):
        geometry = {"type": "GeometryCollection", "geometries": [geometry]}
    source = _write_json(
        tmp_path / "deep.geojson",
        {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": geometry}]},
    )

    report = validate_geojson(source)
    assert not report.valid
    assert "nesting" in report.errors[0]
    with pytest.raises(GeoSplitError, match="nesting"):
        split_geojson(source, tmp_path / "out", features_per_file=1)


@pytest.mark.parametrize(
    "name",
    ["../escape.geojson", "nested/file.geojson", "/absolute.geojson", "C:/absolute.geojson"],
)
def test_manifest_paths_cannot_escape_output_directory(name: str, tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / ".places.geosplit.json").write_text(json.dumps({"files": [name]}), encoding="utf-8")
    source = _write_json(tmp_path / "places.geojson", {"type": "FeatureCollection", "features": []})

    with pytest.raises(GeoSplitError, match="manifest"):
        split_geojson(source, output, features_per_file=1, force=True)


@pytest.mark.parametrize(
    "name",
    ["../escape.geojson", "nested/file.geojson", "/absolute.geojson", "C:/absolute.geojson"],
)
def test_transaction_journal_paths_cannot_escape_output_directory(name: str, tmp_path: Path) -> None:
    output = tmp_path / "out"
    tx = output / ".places.geosplit-transaction"
    tx.mkdir(parents=True)
    (tx / "journal.json").write_text(
        json.dumps({"status": "prepared", "existing": [], "targets": [name]}),
        encoding="utf-8",
    )

    with pytest.raises(GeoSplitError, match="recover"):
        _recover_transaction(output, "places")
