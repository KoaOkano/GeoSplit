"""Command-line interface for GeoSplit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .convert import convert_file, plan_geopackage_split, split_geopackage
from .core import GeoSplitError, SplitPlan, parse_size, plan_split, split_geojson
from .validation import ValidationReport, validate_geojson


def _parse_size(value: str) -> int:
    try:
        return parse_size(value)
    except GeoSplitError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _format_progress(current: int, total: int | None) -> str:
    if total is None:
        return f"{current:,}"
    return f"{current:,} / {total:,}"


class _ProgressDisplay:
    def __init__(self, *, enabled: bool = True) -> None:
        self._stream = sys.stderr
        self._enabled = enabled and self._stream.isatty()
        self._order: list[str] = []
        self._rows: dict[str, tuple[int, int | None]] = {}
        self._rendered_lines = 0

    def update(self, label: str, current: int, total: int | None = None) -> None:
        if not self._enabled:
            return
        if label not in self._rows:
            self._order.append(label)
        self._rows[label] = (current, total)
        self._render()

    def finish(self) -> None:
        if self._enabled and self._rendered_lines:
            self._stream.write("\n")
            self._stream.flush()

    def _render(self) -> None:
        if self._rendered_lines:
            self._stream.write(f"\x1b[{self._rendered_lines}F")
        for label in self._order:
            current, total = self._rows[label]
            self._stream.write(f"{label:<20}{_format_progress(current, total)}\x1b[K\n")
        self._rendered_lines = len(self._order)
        self._stream.flush()


def _build_parsers() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(
        prog="geosplit", description="Split and validate GeoJSON, or convert GeoJSON <-> GeoPackage."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    split = commands.add_parser("split", help="split a GeoJSON FeatureCollection or GeoPackage layer")
    split.add_argument("input", help="source .geojson, .json, or .gpkg file")
    split.add_argument("output_dir", nargs="?", help="output directory (default: <input-name>_split)")
    mode = split.add_mutually_exclusive_group(required=True)
    mode.add_argument("--features", type=int, metavar="COUNT", help="maximum features per file")
    mode.add_argument("--size", type=_parse_size, metavar="SIZE", help="maximum size, e.g. 10MB or 512KiB")
    split.add_argument("--layer", help="source GeoPackage layer")
    split.add_argument("--prefix", help="output filename prefix (default: input filename)")
    split.add_argument("--force", action="store_true", help="replace outputs managed by GeoSplit")
    split.add_argument("--dry-run", "--dryrun", action="store_true", help="show the split plan without writing")
    split.add_argument("--quiet", action="store_true", help="suppress progress and success output")

    validate = commands.add_parser("validate", help="validate a GeoJSON FeatureCollection")
    validate.add_argument("input", help="source .geojson file")
    validate.add_argument("--json", action="store_true", dest="json_output", help="write a JSON report")

    convert = commands.add_parser("convert", help="convert GeoJSON to or from GeoPackage")
    convert.add_argument("input", help="source .geojson, .json, or .gpkg file")
    convert.add_argument("output", help="destination .geojson, .json, or .gpkg file")
    convert.add_argument("--layer", help="source GeoPackage layer")
    convert.add_argument("--output-layer", help="destination GeoPackage layer name")
    convert.add_argument("--force", action="store_true", help="replace the output file")

    help_command = commands.add_parser("help", help="show general or command-specific help")
    help_command.add_argument("topic", nargs="?", choices=("split", "validate", "convert"), help="command to explain")
    return parser, commands.choices


def help_text(topic: str | None = None) -> str:
    """Return general help or detailed help for a command."""
    parser, commands = _build_parsers()
    return (commands[topic] if topic else parser).format_help()


def _print_plan(plan: SplitPlan, force: bool) -> None:
    conflicts = set(plan.conflicts)
    print(f"Source: {plan.source}")
    print(f"Output: {plan.output_dir}")
    print(f"Features: {plan.feature_count:,}")
    total_size = "not estimated" if plan.total_bytes < 0 else f"{plan.total_bytes:,} bytes"
    print(f"Total size: {total_size}")
    print("Files:")
    for item in plan.files:
        status = ""
        if item.path in conflicts:
            status = " [replace]" if force else " [conflict]"
        size = "size not estimated" if item.size < 0 else f"{item.size:,} bytes"
        print(f"  {item.path.name}: {item.feature_count:,} features, {size}{status}")
    if plan.conflicts:
        print("Conflicts:")
        for path in plan.conflicts:
            print(f"  {path}")
    for warning in plan.warnings:
        print(f"Warning: {warning}")


def _is_geopackage(path: str | Path) -> bool:
    return Path(path).suffix.lower() == ".gpkg"


def _run_split(args: argparse.Namespace) -> None:
    package_input = _is_geopackage(args.input)
    if args.layer and not package_input:
        raise GeoSplitError("--layer only applies when splitting a GeoPackage.")

    if args.dry_run:
        if package_input:
            plan = plan_geopackage_split(
                args.input,
                args.output_dir,
                features_per_file=args.features,
                max_bytes=args.size,
                prefix=args.prefix,
                layer=args.layer,
            )
        else:
            plan = plan_split(
                args.input,
                args.output_dir,
                features_per_file=args.features,
                max_bytes=args.size,
                prefix=args.prefix,
            )
        if not args.quiet:
            _print_plan(plan, args.force)
        return

    progress = _ProgressDisplay(enabled=not args.quiet)
    try:
        if package_input:
            result = split_geopackage(
                args.input,
                args.output_dir,
                features_per_file=args.features,
                max_bytes=args.size,
                prefix=args.prefix,
                layer=args.layer,
                force=args.force,
                progress=progress.update,
            )
        else:
            def update_split_progress(feature_count: int, file_count: int) -> None:
                progress.update("Reading features", feature_count)
                progress.update("Writing chunks", file_count)

            result = split_geojson(
                args.input,
                args.output_dir,
                features_per_file=args.features,
                max_bytes=args.size,
                prefix=args.prefix,
                force=args.force,
                progress=None if args.quiet else update_split_progress,
            )
            progress.update("Reading features", result.feature_count, result.feature_count)
            progress.update("Writing chunks", len(result), len(result))
            progress.update("Validating output", sum(path.exists() for path in result.files), len(result))
    finally:
        progress.finish()
    if not args.quiet:
        print(f"Created {len(result)} file(s) in {result.files[0].parent}")


def _run_convert(args: argparse.Namespace) -> None:
    progress = _ProgressDisplay()
    try:
        path = convert_file(
            args.input,
            args.output,
            layer=args.layer,
            output_layer=args.output_layer,
            force=args.force,
            progress=progress.update,
        )
    finally:
        progress.finish()
    print(f"Created {path}")


def _print_validation(report: ValidationReport) -> None:
    if not report.valid:
        print("Invalid GeoJSON")
        for error in report.errors:
            print(f"Error: {error}")
        if report.feature_count:
            print(f"Features checked: {report.feature_count:,}")
        return

    print("Valid GeoJSON FeatureCollection")
    print(f"Features: {report.feature_count:,}")
    print("Geometry types:")
    if report.geometry_counts:
        for geometry_type, count in report.geometry_counts.items():
            print(f"  {geometry_type}: {count:,}")
    else:
        print("  None")
    print(f"Null geometries: {report.null_geometry_count:,}")
    print(f"Maximum nesting: {report.maximum_nesting}")
    dimensions = ", ".join(f"{dimension}D" for dimension in report.coordinate_dimensions) or "None"
    print(f"Coordinate dimensions: {dimensions}")
    print("Coordinate precision: preserved")
    for warning in report.warnings:
        print(f"Warning: {warning}")


def _run_validate(args: argparse.Namespace) -> int:
    progress = _ProgressDisplay(enabled=not args.json_output)

    def update_validation_progress(feature_count: int) -> None:
        if feature_count == 1 or feature_count % 1000 == 0:
            progress.update("Reading features", feature_count)

    try:
        report = validate_geojson(args.input, progress=None if args.json_output else update_validation_progress)
        progress.update("Reading features", report.feature_count, report.feature_count if report.valid else None)
    finally:
        progress.finish()
    if args.json_output:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_validation(report)
    return 0 if report.valid else 1


def main(argv: list[str] | None = None) -> int:
    parser, _ = _build_parsers()
    try:
        args = parser.parse_args(argv)
        if args.command == "help":
            print(help_text(args.topic), end="")
        elif args.command == "split":
            _run_split(args)
        elif args.command == "validate":
            return _run_validate(args)
        else:
            _run_convert(args)
        return 0
    except GeoSplitError as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
