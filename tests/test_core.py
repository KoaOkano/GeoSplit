import json
from pathlib import Path

import pytest

from geo_splitter.core import GeoSplitterError, parse_size, split_geojson


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
    with pytest.raises(GeoSplitterError, match="Feature 1"):
        split_geojson(collection, tmp_path / "out", max_bytes=130)


def test_does_not_overwrite_by_default(collection: Path, tmp_path: Path) -> None:
    split_geojson(collection, tmp_path / "out", features_per_file=5)
    with pytest.raises(GeoSplitterError, match="already exists"):
        split_geojson(collection, tmp_path / "out", features_per_file=5)
