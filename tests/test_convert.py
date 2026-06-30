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
