"""All maintained Python and stubs must retain strict annotations."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_new_python_and_stubs_cannot_escape_project_gates(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_bytes((ROOT / "pyproject.toml").read_bytes())
    names = (
        "entrypoint.py",
        "ci/new_guard.py",
        "actions/release/new_check.py",
        "release_ci/new_binding.pyi",
        "tests/test_new_behavior.py",
        "future_tools/new_tool.py",
    )
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def missing(value):\n    return value\n")
    (tmp_path / "explicit_any.py").write_text(
        "from typing import Any\ndef untyped(value: Any) -> Any:\n    return value\n"
    )
    commands = (
        [
            "pyrefly",
            "check",
            "--config",
            "pyproject.toml",
            "--output-format",
            "min-text",
        ],
        ["ruff", "check", ".", "--output-format", "concise"],
    )
    for command in commands:
        result = subprocess.run(
            [sys.executable, "-m", *command],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        output = (result.stdout + result.stderr).replace("\\", "/")
        assert result.returncode == 1, output
        for name in (*names, "explicit_any.py"):
            assert name in output, output
        if command[0] == "ruff":
            assert "ANN001" in output and "ANN201" in output and "ANN401" in output
        else:
            assert "implicit-any-parameter" in output and "explicit-any" in output
