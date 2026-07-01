import json
from pathlib import Path

import pytest

from geosplit.core import GeoSplitError, parse_size, split_geojson


@pytest.fixture
def collection(tmp_path: Path) -> Path:
    path = tmp_path / "places.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": "places",
                "bbox": [0, 0, 4, 4],
                "features": [
                    {"type": "Feature", "properties": {"id": i}, "geometry": {"type": "Point", "coordinates": [i, i]}}
                    for i in range(5)
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_parse_size() -> None:
    assert parse_size("2 MB") == 2_000_000
    assert parse_size("2MiB") == 2_097_152
    assert parse_size("9007199254740993B") == 9_007_199_254_740_993


def test_split_by_feature_count(collection: Path, tmp_path: Path) -> None:
    paths = split_geojson(collection, tmp_path / "out", features_per_file=2)
    assert [len(json.loads(path.read_text())["features"]) for path in paths] == [2, 2, 1]
    assert all("bbox" not in json.loads(path.read_text()) for path in paths)


def test_split_by_exact_size(collection: Path, tmp_path: Path) -> None:
    paths = split_geojson(collection, tmp_path / "out", max_bytes=260)
    assert len(paths) > 1
    assert sum(len(json.loads(path.read_text())["features"]) for path in paths) == 5
    assert all(path.stat().st_size <= 260 for path in paths)


def test_rejects_oversized_feature(collection: Path, tmp_path: Path) -> None:
    with pytest.raises(GeoSplitError, match="Feature 1"):
        split_geojson(collection, tmp_path / "out", max_bytes=130)


def test_does_not_overwrite_by_default(collection: Path, tmp_path: Path) -> None:
    split_geojson(collection, tmp_path / "out", features_per_file=5)
    with pytest.raises(GeoSplitError, match="already exists"):
        split_geojson(collection, tmp_path / "out", features_per_file=5)


def test_force_removes_stale_parts(collection: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    split_geojson(collection, output, features_per_file=2)
    paths = split_geojson(collection, output, features_per_file=3, force=True)
    assert list(output.glob("*.geojson")) == paths


def test_rejects_prefix_paths(collection: Path, tmp_path: Path) -> None:
    with pytest.raises(GeoSplitError, match="filename"):
        split_geojson(collection, tmp_path / "out", features_per_file=2, prefix="../escape")


@pytest.mark.parametrize(
    "feature",
    [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Unknown", "coordinates": []}},
        {"type": "Feature", "properties": {}},
        {"type": "NotAFeature", "properties": {}, "geometry": None},
    ],
)
def test_rejects_invalid_features(feature: object, tmp_path: Path) -> None:
    source = tmp_path / "invalid.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}), encoding="utf-8")
    with pytest.raises(GeoSplitError, match="Feature 1"):
        split_geojson(source, tmp_path / "out", features_per_file=1)


def test_preserves_metadata_after_features(tmp_path: Path) -> None:
    source = tmp_path / "ordered.geojson"
    source.write_text(
        '{"type":"FeatureCollection","features":[],"name":"after","bbox":[0,0,1,1]}', encoding="utf-8"
    )
    [output] = split_geojson(source, tmp_path / "out", features_per_file=1)
    result = json.loads(output.read_text())
    assert result["name"] == "after"
    assert "bbox" not in result


def test_failed_split_preserves_existing_output(collection: Path, tmp_path: Path, monkeypatch) -> None:
    output_dir = tmp_path / "out"
    [output] = split_geojson(collection, output_dir, features_per_file=5)
    original = output.read_bytes()

    def fail(*args, **kwargs):
        raise GeoSplitError("invalid feature")

    monkeypatch.setattr("geosplit.core._validate_geometry", fail)
    with pytest.raises(GeoSplitError, match="invalid feature"):
        split_geojson(collection, output_dir, features_per_file=5, force=True)
    assert output.read_bytes() == original


def test_rejects_input_matching_managed_output(tmp_path: Path) -> None:
    source = tmp_path / "data_001.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    with pytest.raises(GeoSplitError, match="input"):
        split_geojson(source, tmp_path, features_per_file=1, prefix="data", force=True)


def test_wraps_output_errors(collection: Path, tmp_path: Path) -> None:
    output = tmp_path / "not-a-directory"
    output.write_text("file", encoding="utf-8")
    with pytest.raises(GeoSplitError, match="Cannot write"):
        split_geojson(collection, output, features_per_file=1)


def test_accepts_utf8_bom(tmp_path: Path) -> None:
    source = tmp_path / "bom.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8-sig")
    assert split_geojson(source, tmp_path / "out", features_per_file=1)
