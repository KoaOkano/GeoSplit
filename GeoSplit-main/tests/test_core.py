import json
import errno
import os
import tracemalloc
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from geosplit.core import GeoSplitError, iter_batches, parse_size, plan_split, split_geojson


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
    assert list(output.glob("*.geojson")) == list(paths)


def test_force_preserves_unmanaged_matching_files(collection: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    split_geojson(collection, output, features_per_file=2, prefix="places")
    unmanaged = output / "places_999.geojson"
    unmanaged.write_text("not created by GeoSplit", encoding="utf-8")
    split_geojson(collection, output, features_per_file=3, prefix="places", force=True)
    assert unmanaged.read_text(encoding="utf-8") == "not created by GeoSplit"


def test_force_rejects_corrupt_manifest(collection: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / ".places.geosplit.json").write_text('{"files":["../outside.geojson"]}', encoding="utf-8")
    with pytest.raises(GeoSplitError, match="manifest"):
        split_geojson(collection, output, features_per_file=2, force=True)


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
    source.write_text('{"type":"FeatureCollection","features":[],"name":"after","bbox":[0,0,1,1]}', encoding="utf-8")
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
    with pytest.raises(GeoSplitError, match="not a directory"):
        split_geojson(collection, output, features_per_file=1)


def test_accepts_utf8_bom(tmp_path: Path) -> None:
    source = tmp_path / "bom.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8-sig")
    assert split_geojson(source, tmp_path / "out", features_per_file=1)


def test_preserves_coordinate_precision(tmp_path: Path) -> None:
    coordinate = "139.12345678901234567890123456789"
    source = tmp_path / "precision.geojson"
    source.write_text(
        '{"type":"FeatureCollection","features":[{"type":"Feature","properties":{},'
        f'"geometry":{{"type":"Point","coordinates":[{coordinate},35.0]}}}}]}}',
        encoding="utf-8",
    )
    [output] = split_geojson(source, tmp_path / "out", features_per_file=1)
    document = json.loads(output.read_text(encoding="utf-8"), parse_float=Decimal)
    assert document["features"][0]["geometry"]["coordinates"][0] == Decimal(coordinate)


def test_handles_unicode_paths(tmp_path: Path) -> None:
    source = tmp_path / "日本の地点.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    [output] = split_geojson(source, tmp_path / "出力", features_per_file=1)
    assert output.name == "日本の地点_001.geojson"


@pytest.mark.parametrize("content", ["{", '{"type":"FeatureCollection","features":[]} trailing'])
def test_rejects_corrupt_files(content: str, tmp_path: Path) -> None:
    source = tmp_path / "corrupt.geojson"
    source.write_text(content, encoding="utf-8")
    with pytest.raises(GeoSplitError, match="GeoJSON|valid"):
        split_geojson(source, tmp_path / "out", features_per_file=1)


def test_splits_large_file(tmp_path: Path) -> None:
    source = tmp_path / "large.geojson"
    with source.open("w", encoding="utf-8") as stream:
        stream.write('{"type":"FeatureCollection","features":[')
        for index in range(50_000):
            if index:
                stream.write(",")
            stream.write(
                f'{{"type":"Feature","properties":{{"id":{index}}},'
                f'"geometry":{{"type":"Point","coordinates":[{index},{index}]}}}}'
            )
        stream.write("]}")

    tracemalloc.start()
    result = split_geojson(source, tmp_path / "out", features_per_file=1_000)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(result) == 50
    assert result.feature_count == 50_000
    assert peak < 20_000_000


def test_plan_split_does_not_write(collection: Path, tmp_path: Path) -> None:
    output = tmp_path / "missing"
    plan = plan_split(collection, output, features_per_file=2)
    assert not output.exists()
    assert [item.feature_count for item in plan.files] == [2, 2, 1]
    assert plan.feature_count == 5
    assert plan.total_bytes == sum(item.size for item in plan.files)


def test_default_output_directory(collection: Path) -> None:
    result = split_geojson(collection, features_per_file=5)
    assert result.files[0].parent == collection.with_name("places_split")
    assert result.feature_count == 5
    assert result.total_bytes == result.files[0].stat().st_size


def test_iter_batches_and_early_cleanup(collection: Path) -> None:
    batches = iter_batches(collection, features=2)
    assert len(next(batches)["features"]) == 2
    batches.close()
    renamed = collection.with_name("renamed.geojson")
    collection.rename(renamed)
    assert renamed.exists()


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
def test_accepts_every_geometry_type(geometry: object, tmp_path: Path) -> None:
    source = tmp_path / "geometry.geojson"
    source.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {}, "geometry": geometry}],
            }
        ),
        encoding="utf-8",
    )
    assert split_geojson(source, tmp_path / "out", features_per_file=1)


@pytest.mark.parametrize(
    "geometry",
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
def test_accepts_empty_geometries(geometry: object, tmp_path: Path) -> None:
    source = tmp_path / "empty-geometry.geojson"
    source.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {}, "geometry": geometry}],
            }
        ),
        encoding="utf-8",
    )
    assert split_geojson(source, tmp_path / "out", features_per_file=1)


@pytest.mark.parametrize(
    "coordinates",
    [
        ["longitude", 35],
        [139],
        [[139, 35]],
        [True, 35],
    ],
)
def test_rejects_invalid_point_coordinates(coordinates: object, tmp_path: Path) -> None:
    source = tmp_path / "invalid-coordinate.geojson"
    source.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {"type": "Point", "coordinates": coordinates},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(GeoSplitError, match="coordinates"):
        split_geojson(source, tmp_path / "out", features_per_file=1)


def test_rejects_excessive_nesting(tmp_path: Path) -> None:
    source = tmp_path / "deep.geojson"
    nested = "[" * 101 + "0" + "]" * 101
    source.write_text(f'{{"type":"FeatureCollection","metadata":{nested},"features":[]}}', encoding="utf-8")
    with pytest.raises(GeoSplitError, match="nesting"):
        split_geojson(source, tmp_path / "out", features_per_file=1)


def test_recovers_interrupted_transaction(collection: Path, tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    result = split_geojson(collection, output_dir, features_per_file=5)
    output = result.files[0]
    manifest = output_dir / ".places.geosplit.json"
    original = output.read_bytes()
    tx = output_dir / ".places.geosplit-transaction"
    backups = tx / "backups"
    backups.mkdir(parents=True)
    (tx / "journal.json").write_text(
        json.dumps(
            {
                "status": "prepared",
                "existing": [output.name, manifest.name],
                "targets": [output.name, manifest.name],
            }
        ),
        encoding="utf-8",
    )
    output.replace(backups / output.name)
    manifest.replace(backups / manifest.name)
    output.write_text("partial", encoding="utf-8")

    with pytest.raises(GeoSplitError, match="already exists"):
        split_geojson(collection, output_dir, features_per_file=5)
    assert output.read_bytes() == original
    assert manifest.exists()
    assert not tx.exists()


def test_adopts_pre_manifest_outputs(collection: Path, tmp_path: Path) -> None:
    output = tmp_path / "out"
    first = split_geojson(collection, output, features_per_file=2)
    (output / ".places.geosplit.json").unlink()
    result = split_geojson(collection, output, features_per_file=3, force=True)
    assert len(first) == 3
    assert len(result) == 2
    assert sorted(output.glob("*.geojson")) == list(result)
    assert (output / ".places.geosplit.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific locked-file behavior")
def test_reports_locked_windows_output(collection: Path, tmp_path: Path, monkeypatch) -> None:
    output_dir = tmp_path / "out"
    output = split_geojson(collection, output_dir, features_per_file=5).files[0]
    original_replace = Path.replace

    def locked(path: Path, target: Path):
        if path == output:
            raise PermissionError(13, "file is locked", str(path))
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", locked)
    with pytest.raises(GeoSplitError, match="locked"):
        split_geojson(collection, output_dir, features_per_file=5, force=True)


def test_reports_full_disk(collection: Path, tmp_path: Path, monkeypatch) -> None:
    original_write = Path.write_bytes

    def full(path: Path, data: bytes):
        if path.parent.name == "new":
            raise OSError(errno.ENOSPC, "disk full")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", full)
    with pytest.raises(GeoSplitError, match="Disk is full"):
        split_geojson(collection, tmp_path / "out", features_per_file=5)


def test_disk_preflight_fails_before_writing(collection: Path, tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "out"
    monkeypatch.setattr("geosplit.core.shutil.disk_usage", lambda path: SimpleNamespace(free=0))
    with pytest.raises(GeoSplitError, match="Insufficient disk space"):
        split_geojson(collection, output, features_per_file=5)
    assert not output.exists()


def test_normal_split_opens_input_twice(collection: Path, tmp_path: Path, monkeypatch) -> None:
    from geosplit import core

    original = core._open
    opened = 0

    def counted(path: Path):
        nonlocal opened
        opened += 1
        return original(path)

    monkeypatch.setattr(core, "_open", counted)
    split_geojson(collection, tmp_path / "out", features_per_file=2)
    assert opened == 2
