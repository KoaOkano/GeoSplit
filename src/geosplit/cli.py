"""Command-line interface for GeoSplit."""

from __future__ import annotations

import argparse
import sys

from .convert import convert_file
from .core import GeoSplitError, parse_size, split_geojson


def _parse_size(value: str) -> int:
    try:
        return parse_size(value)
    except GeoSplitError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _parsers() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(
        prog="geosplit", description="Split GeoJSON and convert GeoJSON <-> GeoPackage."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    split = commands.add_parser("split", help="split a GeoJSON FeatureCollection")
    split.add_argument("input", help="source .geojson file")
    split.add_argument("output_dir", help="directory for split files")
    mode = split.add_mutually_exclusive_group(required=True)
    mode.add_argument("--features", type=int, metavar="COUNT", help="maximum features per file")
    mode.add_argument("--size", type=_parse_size, metavar="SIZE", help="maximum size, e.g. 10MB or 512KiB")
    split.add_argument("--prefix", help="output filename prefix (default: input filename)")
    split.add_argument("--force", action="store_true", help="replace matching output files")

    convert = commands.add_parser("convert", help="convert GeoJSON to or from GeoPackage")
    convert.add_argument("input", help="source .geojson, .json, or .gpkg file")
    convert.add_argument("output", help="destination .geojson, .json, or .gpkg file")
    convert.add_argument("--layer", help="source GeoPackage layer")
    convert.add_argument("--output-layer", help="destination GeoPackage layer name")
    convert.add_argument("--force", action="store_true", help="replace the output file")

    help_command = commands.add_parser("help", help="show general or command-specific help")
    help_command.add_argument("topic", nargs="?", choices=("split", "convert"), help="command to explain")
    return parser, commands.choices


def help_text(topic: str | None = None) -> str:
    """Return general help or detailed help for a command."""
    parser, commands = _parsers()
    return (commands[topic] if topic else parser).format_help()


def main(argv: list[str] | None = None) -> int:
    parser, _ = _parsers()
    try:
        args = parser.parse_args(argv)
        if args.command == "help":
            print(help_text(args.topic), end="")
        elif args.command == "split":
            paths = split_geojson(
                args.input,
                args.output_dir,
                features_per_file=args.features,
                max_bytes=args.size,
                prefix=args.prefix,
                force=args.force,
            )
            print(f"Created {len(paths)} file(s) in {paths[0].parent}")
        else:
            path = convert_file(
                args.input, args.output, layer=args.layer, output_layer=args.output_layer, force=args.force
            )
            print(f"Created {path}")
        return 0
    except GeoSplitError as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
