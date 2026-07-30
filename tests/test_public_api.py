import geosplit
from geosplit import (
    GeoSplitError,
    SplitPlan,
    SplitResult,
    ValidationReport,
    convert,
    iter_batches,
    parse_size,
    plan_split,
    split_geojson,
    validate_geojson,
)
from geosplit.convert import convert_file, plan_geopackage_split, split_geopackage

PUBLIC_API = (
    GeoSplitError,
    SplitPlan,
    SplitResult,
    ValidationReport,
    iter_batches,
    parse_size,
    plan_split,
    split_geojson,
    validate_geojson,
)
CONVERSION_API = (convert_file, plan_geopackage_split, split_geopackage)


def test_top_level_public_api() -> None:
    assert geosplit.__all__ == [item.__name__ for item in PUBLIC_API]
    assert issubclass(GeoSplitError, ValueError)
    assert parse_size("1MiB") == 1_048_576


def test_conversion_public_api() -> None:
    assert convert.__all__ == [item.__name__ for item in CONVERSION_API]
