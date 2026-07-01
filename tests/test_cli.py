import json
from pathlib import Path

import pytest

from geosplit.cli import help_text, main


def test_split_command(tmp_path: Path, capsys) -> None:
    source = tmp_path / "data.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    assert main(["split", str(source), str(tmp_path / "out"), "--features", "10"]) == 0
    assert "Created 1 file" in capsys.readouterr().out


def test_help_command(capsys) -> None:
    assert main(["help", "split"]) == 0
    assert "--features" in capsys.readouterr().out
    assert "convert" in help_text()


def test_invalid_size_has_useful_error(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["split", "input.geojson", "out", "--size", "nonsense"])
    assert "try 500KB, 2MB, or 1GiB" in capsys.readouterr().err
