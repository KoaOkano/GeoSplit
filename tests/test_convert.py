import json
from pathlib import Path

import pytest

from geosplit.convert import convert_file
from geosplit.core import GeoSplitError


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
    source = Path(__file__).parent / "fixtures" / "sample.geojson"
    package = convert_file(source, tmp_path / "sample.gpkg", output_layer="places")
    output = convert_file(package, tmp_path / "round-trip.geojson", layer="places")
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["type"] == "FeatureCollection"
    assert len(document["features"]) == 5


def test_failed_conversion_preserves_destination(tmp_path: Path, monkeypatch) -> None:
    import geopandas as gpd

    source = Path(__file__).parent / "fixtures" / "sample.geojson"
    destination = tmp_path / "existing.gpkg"
    destination.write_bytes(b"existing")

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(gpd.GeoDataFrame, "to_file", fail)
    with pytest.raises(GeoSplitError, match="Conversion failed"):
        convert_file(source, destination, force=True)
    assert destination.read_bytes() == b"existing"
