import json
from pathlib import Path

import pytest

from geosplit.convert import convert_file, plan_geopackage_split, split_geopackage
from geosplit.core import GeoSplitError


class _LayerNames:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def tolist(self) -> list[str]:
        return self._names


class _LayerTable:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def __getitem__(self, key: str) -> _LayerNames:
        assert key == "name"
        return _LayerNames(self._names)


class _Iloc:
    def __init__(self, frame: "_Frame") -> None:
        self._frame = frame

    def __getitem__(self, item: slice) -> "_Frame":
        return _Frame(self._frame.rows[item])


class _Frame:
    def __init__(self, rows: list[int]) -> None:
        self.rows = rows
        self.iloc = _Iloc(self)

    def __len__(self) -> int:
        return len(self.rows)

    def to_file(self, path: Path, *, driver: str, layer: str, index: bool) -> None:
        assert driver == "GPKG"
        assert index is False
        path.write_text(json.dumps({"layer": layer, "rows": self.rows}), encoding="utf-8")


class _GeoPandas:
    def __init__(self, *, layers: list[str] | None = None, rows: list[int] | None = None) -> None:
        self.layers = layers or ["roads"]
        self.rows = rows if rows is not None else [1, 2, 3, 4, 5]

    def list_layers(self, source: Path) -> _LayerTable:
        return _LayerTable(self.layers)

    def read_file(self, source: Path, *, layer: str) -> _Frame:
        assert layer in self.layers
        return _Frame(self.rows)


def _source_package(tmp_path: Path) -> Path:
    source = tmp_path / "roads.gpkg"
    source.write_bytes(b"fake gpkg")
    return source


def test_requires_geojson_and_geopackage(tmp_path: Path) -> None:
    source = tmp_path / "input.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    with pytest.raises(GeoSplitError, match="requires one"):
        convert_file(source, tmp_path / "output.json")


def test_rejects_layer_for_geojson_input(tmp_path: Path) -> None:
    source = tmp_path / "input.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    with pytest.raises(GeoSplitError, match="only applies"):
        convert_file(source, tmp_path / "output.gpkg", layer="places")


def test_round_trip_geojson_and_geopackage(tmp_path: Path) -> None:
    pytest.importorskip("geopandas")
    source = Path(__file__).parent / "fixtures" / "sample.geojson"
    package = convert_file(source, tmp_path / "sample.gpkg", output_layer="places")
    output = convert_file(package, tmp_path / "round-trip.geojson", layer="places")
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["type"] == "FeatureCollection"
    assert len(document["features"]) == 5


def test_failed_conversion_preserves_destination(tmp_path: Path, monkeypatch) -> None:
    gpd = pytest.importorskip("geopandas")

    source = Path(__file__).parent / "fixtures" / "sample.geojson"
    destination = tmp_path / "existing.gpkg"
    destination.write_bytes(b"existing")

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(gpd.GeoDataFrame, "to_file", fail)
    with pytest.raises(GeoSplitError, match="Conversion failed"):
        convert_file(source, destination, force=True)
    assert destination.read_bytes() == b"existing"


def test_plan_geopackage_split_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("geosplit.convert._geopandas", lambda: _GeoPandas())
    source = _source_package(tmp_path)
    output = tmp_path / "out"
    plan = plan_geopackage_split(source, output, features_per_file=2)
    assert not output.exists()
    assert [item.path.name for item in plan.files] == ["roads_001.gpkg", "roads_002.gpkg", "roads_003.gpkg"]
    assert [item.feature_count for item in plan.files] == [2, 2, 1]
    assert plan.total_bytes == -1
    assert "not estimated" in plan.warnings[-1]


def test_split_geopackage_preserves_layer_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("geosplit.convert._geopandas", lambda: _GeoPandas(rows=[0, 1, 2, 3, 4]))
    source = _source_package(tmp_path)
    output = tmp_path / "out"
    result = split_geopackage(source, output, features_per_file=2)

    assert [path.name for path in result.files] == ["roads_001.gpkg", "roads_002.gpkg", "roads_003.gpkg"]
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in result.files]
    assert [document["rows"] for document in documents] == [[0, 1], [2, 3], [4]]
    assert {document["layer"] for document in documents} == {"roads"}
    assert result.feature_count == 5
    assert (output / ".roads.geosplit.json").exists()


def test_split_geopackage_requires_layer_when_ambiguous(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("geosplit.convert._geopandas", lambda: _GeoPandas(layers=["roads", "buildings"]))
    with pytest.raises(GeoSplitError, match="multiple layers"):
        split_geopackage(_source_package(tmp_path), tmp_path / "out", features_per_file=2)
    assert not (tmp_path / "out").exists()


def test_split_geopackage_rejects_size_mode() -> None:
    with pytest.raises(GeoSplitError, match="Size-based splitting is not supported"):
        split_geopackage("roads.gpkg", max_bytes=100)
