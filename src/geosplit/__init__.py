"""Split GeoJSON files and convert GeoJSON to or from GeoPackage."""

from .core import GeoSplitError, split_geojson

__all__ = ["GeoSplitError", "split_geojson"]
__version__ = "0.2.0"
