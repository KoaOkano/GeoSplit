"""Split GeoJSON files and convert GeoJSON to or from GeoPackage."""

from .core import GeoSplitterError, split_geojson

__all__ = ["GeoSplitterError", "split_geojson"]
__version__ = "0.1.0"
