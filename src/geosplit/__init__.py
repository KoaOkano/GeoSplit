"""Public Python API for GeoSplit."""

from .core import GeoSplitError, SplitPlan, SplitResult, iter_batches, parse_size, plan_split, split_geojson
from .validation import ValidationReport, validate_geojson

__version__ = "0.7.0"
__all__ = [
    "GeoSplitError",
    "SplitPlan",
    "SplitResult",
    "ValidationReport",
    "iter_batches",
    "parse_size",
    "plan_split",
    "split_geojson",
    "validate_geojson",
]
