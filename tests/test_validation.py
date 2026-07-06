import json
import tracemalloc
from pathlib import Path

import pytest

from geosplit import ValidationReport, validate_geojson
from geosplit.cli import help_text, main


def _write_collection(path: Path, geometries: list[object]) -> Path:
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "properties": {"id": index}, "geometry": geometry}
                    for index, geometry in enumerate(geometries)
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_validation_report_for_valid_collection(tmp_path: Path) -> None:
    source = _write_collection(
        tmp_path / "mixed.geojson",
        [
            {"type": "Point", "coordinates": [1, 2]},
            {"type": "Point", "coordinates": [1, 2, 3]},
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            None,
        ],
    )

    report = validate_geojson(source)

    assert isinstance(report, ValidationReport)
    assert report.valid
    assert report.feature_count == 4
    assert report.geometry_counts == {"Point": 2, "Polygon": 1}
    assert report.null_geometry_count == 1
    assert report.coordinate_dimensions == (2, 3)
    assert report.maximum_nesting >= 5
    assert report.warnings == ("Mixed coordinate dimensions found: 2, 3.",)
    assert report.errors == ()


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [1, 2]},
        {"type": "MultiPoint", "coordinates": [[1, 2], [3, 4]]},
        {"type": "LineString", "coordinates": [[1, 2], [3, 4]]},
        {"type": "MultiLineString", "coordinates": [[[1, 2], [3, 4]]]},
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        {"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]]},
        {"type": "GeometryCollection", "geometries": [{"type": "Point", "coordinates": [1, 2]}]},
    ],
)
def test_validation_accepts_every_geometry_type(geometry: object, tmp_path: Path) -> None:
    source = _write_collection(tmp_path / "geometry.geojson", [geometry])
    assert validate_geojson(source).valid


def test_validation_accepts_empty_geometries(tmp_path: Path) -> None:
    source = _write_collection(
        tmp_path / "empty.geojson",
        [
            {"type": "Point", "coordinates": []},
            {"type": "MultiPoint", "coordinates": []},
            {"type": "LineString", "coordinates": []},
            {"type": "MultiLineString", "coordinates": []},
            {"type": "Polygon", "coordinates": []},
            {"type": "MultiPolygon", "coordinates": []},
            {"type": "GeometryCollection", "geometries": []},
        ],
    )
    assert validate_geojson(source).valid


def test_validation_writes_nothing(tmp_path: Path) -> None:
    source = _write_collection(tmp_path / "input.geojson", [{"type": "Point", "coordinates": [1, 2]}])
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    validate_geojson(source)
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before


@pytest.mark.parametrize(
    ("geometry", "message"),
    [
        ({"type": "Point", "coordinates": ["bad", 2]}, "geometry.coordinates[0]"),
        (
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [2, 2]]]},
            "polygon ring must be closed",
        ),
    ],
)
def test_validation_reports_coordinate_location(geometry: object, message: str, tmp_path: Path) -> None:
    source = _write_collection(tmp_path / "invalid.geojson", [geometry])
    report = validate_geojson(source)
    assert not report.valid
    assert "Feature 1" in report.errors[0]
    assert message in report.errors[0]


@pytest.mark.parametrize("content", ["{", '{"type":"FeatureCollection","features":[]} trailing'])
def test_validation_reports_corrupt_json(content: str, tmp_path: Path) -> None:
    source = tmp_path / "corrupt.geojson"
    source.write_text(content, encoding="utf-8")
    report = validate_geojson(source)
    assert not report.valid
    assert report.errors


def test_validation_rejects_excessive_nesting(tmp_path: Path) -> None:
    source = tmp_path / "deep.geojson"
    nested = "[" * 101 + "0" + "]" * 101
    source.write_text(f'{{"type":"FeatureCollection","metadata":{nested},"features":[]}}', encoding="utf-8")
    report = validate_geojson(source)
    assert not report.valid
    assert "nesting" in report.errors[0]


def test_validation_tracks_features_before_error(tmp_path: Path) -> None:
    source = _write_collection(
        tmp_path / "partial.geojson",
        [
            {"type": "Point", "coordinates": [1, 2]},
            {"type": "Point", "coordinates": [3, 4]},
            {"type": "Point", "coordinates": ["bad", 5]},
        ],
    )
    report = validate_geojson(source)
    assert not report.valid
    assert report.feature_count == 2


def test_validation_json_cli(tmp_path: Path, capsys) -> None:
    source = _write_collection(tmp_path / "input.geojson", [{"type": "Point", "coordinates": [1, 2]}])
    assert main(["validate", str(source), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert report["feature_count"] == 1
    assert report["geometry_types"] == {"Point": 1}
    assert report["coordinate_precision"] == "preserved"


def test_validation_human_cli_and_help(tmp_path: Path, capsys) -> None:
    source = _write_collection(tmp_path / "input.geojson", [None])
    assert main(["validate", str(source)]) == 0
    output = capsys.readouterr().out
    assert "Valid GeoJSON FeatureCollection" in output
    assert "Null geometries: 1" in output
    assert "--json" in help_text("validate")


def test_invalid_validation_cli_returns_one(tmp_path: Path, capsys) -> None:
    source = _write_collection(tmp_path / "invalid.geojson", [{"type": "Point", "coordinates": [1]}])
    assert main(["validate", str(source)]) == 1
    assert "Invalid GeoJSON" in capsys.readouterr().out


def test_validation_memory_is_bounded(tmp_path: Path) -> None:
    source = tmp_path / "large.geojson"
    with source.open("w", encoding="utf-8") as stream:
        stream.write('{"type":"FeatureCollection","features":[')
        for index in range(20_000):
            if index:
                stream.write(",")
            stream.write(
                f'{{"type":"Feature","properties":{{"id":{index}}},'
                f'"geometry":{{"type":"Point","coordinates":[{index},{index}]}}}}'
            )
        stream.write("]}")

    tracemalloc.start()
    report = validate_geojson(source)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert report.valid
    assert report.feature_count == 20_000
    assert peak < 10_000_000
