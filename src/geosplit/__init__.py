"""Split GeoJSON files and convert GeoJSON to or from GeoPackage."""

from .core import GeoSplitError, SplitPlan, SplitResult, iter_batches, plan_split, split_geojson

__version__ = "0.4.0"
__all__ = [
    "GeoSplitError",
    "SplitPlan",
    "SplitResult",
    "iter_batches",
    "plan_split",
    "split_geojson",
]
