from pathlib import Path

import pytest

from geo_splitter.convert import convert_file
from geo_splitter.core import GeoSplitterError


def test_requires_geojson_and_geopackage(tmp_path: Path) -> None:
    source = tmp_path / "input.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    with pytest.raises(GeoSplitterError, match="requires one"):
        convert_file(source, tmp_path / "output.json")


def test_rejects_layer_for_geojson_input(tmp_path: Path) -> None:
    source = tmp_path / "input.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    with pytest.raises(GeoSplitterError, match="only applies"):
        convert_file(source, tmp_path / "output.gpkg", layer="places")
