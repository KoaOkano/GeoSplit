import json
import sys
from pathlib import Path

import pytest

from geosplit import __version__, split_geojson
from geosplit.cli import help_text, main


def test_split_command(tmp_path: Path, capsys) -> None:
    source = tmp_path / "data.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    assert main(["split", str(source), str(tmp_path / "out"), "--features", "10"]) == 0
    assert "Created 1 file" in capsys.readouterr().out


def test_help_command(capsys) -> None:
    assert main(["help", "split"]) == 0
    output = capsys.readouterr().out
    assert "--features" in output
    assert "--dryrun" in output
    assert "--layer" in output
    assert "convert" in help_text()


def test_invalid_size_has_useful_error(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["split", "input.geojson", "out", "--size", "nonsense"])
    assert "try 500KB, 2MB, or 1GiB" in capsys.readouterr().err


def test_dry_run_writes_nothing(tmp_path: Path, capsys) -> None:
    source = tmp_path / "data.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    assert main(["split", str(source), "--features", "10", "--dryrun"]) == 0
    assert not source.with_name("data_split").exists()
    output = capsys.readouterr().out
    assert "Features: 0" in output
    assert "data_001.geojson" in output
    assert "bytes" in output


def test_dry_run_reports_legacy_conflicts_and_warnings(tmp_path: Path, capsys) -> None:
    source = tmp_path / "data.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    output = tmp_path / "out"
    split_geojson(source, output, features_per_file=10)
    (output / ".data.geosplit.json").unlink()
    assert main(["split", str(source), str(output), "--features", "10", "--dry-run"]) == 0
    report = capsys.readouterr().out
    assert "Conflicts:" in report
    assert "Warning: Pre-manifest" in report


def test_quiet_suppresses_split_output(tmp_path: Path, capsys) -> None:
    source = tmp_path / "data.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    assert main(["split", str(source), "--features", "10", "--quiet"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_progress_output(tmp_path: Path, capsys, monkeypatch) -> None:
    source = tmp_path / "data.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    assert main(["split", str(source), "--features", "10"]) == 0
    err = capsys.readouterr().err
    assert "Reading features" in err
    assert "Writing chunks" in err
    assert "Validating output" in err


def test_geopackage_size_split_has_clear_error(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["split", "input.gpkg", "--size", "10MB"])
    assert (
        "Size-based splitting is not supported for GeoPackage input. Use --features instead."
        in capsys.readouterr().err
    )


def test_rejects_layer_for_geojson_split(tmp_path: Path, capsys) -> None:
    source = tmp_path / "data.geojson"
    source.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["split", str(source), "--features", "10", "--layer", "roads"])
    assert "--layer only applies when splitting a GeoPackage" in capsys.readouterr().err


def test_version_option(capsys) -> None:
    with pytest.raises(SystemExit, match="0"):
        main(["--version"])
    assert capsys.readouterr().out.strip() == f"geosplit {__version__}"
