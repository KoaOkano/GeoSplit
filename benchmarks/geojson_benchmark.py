"""Small GeoJSON benchmarks for development and CI."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import threading
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from geosplit import validate_geojson
from geosplit.core import parse_size, split_geojson


@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    geometry: str
    features: int
    seconds: float
    peak_memory_bytes: int
    peak_disk_bytes: int
    output_bytes: int
    output_files: int


def _geometry(kind: str, index: int) -> dict[str, Any]:
    if kind == "point":
        return {"type": "Point", "coordinates": [index, index]}
    if kind == "line":
        return {"type": "LineString", "coordinates": [[index, index], [index + 1, index + 1]]}
    if kind == "polygon":
        return {"type": "Polygon", "coordinates": [[[0, 0], [index + 1, 0], [index + 1, 1], [0, 0]]]}
    return _geometry(("point", "line", "polygon")[index % 3], index)


def write_collection(path: Path, *, features: int, geometry: str) -> None:
    with path.open("w", encoding="utf-8") as stream:
        stream.write('{"type":"FeatureCollection","features":[')
        for index in range(features):
            if index:
                stream.write(",")
            feature = {"type": "Feature", "properties": {"id": index}, "geometry": _geometry(geometry, index)}
            stream.write(json.dumps(feature, separators=(",", ":")))
        stream.write("]}")


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            pass
    return total


def _sample_disk(path: Path, stop: threading.Event, peak: list[int]) -> None:
    while not stop.wait(0.02):
        peak[0] = max(peak[0], _directory_size(path))


def _measure(name: str, output: Path, geometry: str, features: int, operation: Callable[[], int]) -> BenchmarkResult:
    stop = threading.Event()
    peak_disk = [0]
    sampler = threading.Thread(target=_sample_disk, args=(output, stop, peak_disk), daemon=True)

    tracemalloc.start()
    started = time.perf_counter()
    sampler.start()
    output_files = operation()
    stop.set()
    sampler.join()
    seconds = time.perf_counter() - started
    _, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_disk[0] = max(peak_disk[0], _directory_size(output))
    return BenchmarkResult(
        name=name,
        geometry=geometry,
        features=features,
        seconds=seconds,
        peak_memory_bytes=peak_memory,
        peak_disk_bytes=peak_disk[0],
        output_bytes=_directory_size(output),
        output_files=output_files,
    )


def _run_in_workspace(
    root: Path, features: int, geometry: str, operation: str, size: str, chunk: int
) -> list[BenchmarkResult]:
    source = root / f"{geometry}.geojson"
    write_collection(source, features=features, geometry=geometry)
    results = []

    if operation in {"all", "split-count"}:
        output = root / "split-count"
        results.append(
            _measure(
                "split-count",
                output,
                geometry,
                features,
                lambda: len(split_geojson(source, output, features_per_file=chunk, force=True)),
            )
        )

    if operation in {"all", "split-size"}:
        output = root / "split-size"
        results.append(
            _measure(
                "split-size",
                output,
                geometry,
                features,
                lambda: len(split_geojson(source, output, max_bytes=parse_size(size), force=True)),
            )
        )

    if operation in {"all", "validate"}:
        output = root / "validate"
        output.mkdir()

        def validate() -> int:
            report = validate_geojson(source)
            if not report.valid:
                raise RuntimeError(report.errors[0])
            return 0

        results.append(
            _measure(
                "validate",
                output,
                geometry,
                features,
                validate,
            )
        )

    return results


def run_benchmarks(
    features: int, geometry: str, operation: str, size: str, chunk: int, work_dir: Path | None = None
) -> list[BenchmarkResult]:
    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="geosplit-bench-") as workspace:
            return _run_in_workspace(Path(workspace), features, geometry, operation, size, chunk)

    root = work_dir / "geosplit-benchmark-run"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    return _run_in_workspace(root, features, geometry, operation, size, chunk)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run small GeoSplit benchmarks.")
    parser.add_argument("--features", type=int, default=2_000)
    parser.add_argument("--geometry", choices=("point", "line", "polygon", "mixed"), default="mixed")
    parser.add_argument("--operation", choices=("all", "split-count", "split-size", "validate"), default="all")
    parser.add_argument("--chunk", type=int, default=500)
    parser.add_argument("--size", default="256KB")
    parser.add_argument("--work-dir", type=Path, help="directory for temporary benchmark files")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)

    results = run_benchmarks(args.features, args.geometry, args.operation, args.size, args.chunk, args.work_dir)
    data = [asdict(result) for result in results]
    if args.json_output:
        print(json.dumps(data, indent=2))
    else:
        for result in results:
            print(
                f"{result.name}: {result.seconds:.3f}s, "
                f"peak memory {result.peak_memory_bytes:,} bytes, "
                f"peak disk {result.peak_disk_bytes:,} bytes"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
