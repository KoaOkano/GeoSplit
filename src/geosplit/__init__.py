"""Split GeoJSON files and convert GeoJSON to or from GeoPackage."""

from .core import GeoSplitError, SplitPlan, SplitResult, iter_batches, plan_split, split_geojson
from .validation import ValidationReport, validate_geojson

__version__ = "0.5.5"
__all__ = [
    "GeoSplitError",
    "SplitPlan",
    "SplitResult",
    "ValidationReport",
    "iter_batches",
    "plan_split",
    "split_geojson",
    "validate_geojson",
]
