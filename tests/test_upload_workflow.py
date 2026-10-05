"""Uploader sidecars must never contaminate frozen distributions."""

import os
import re
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_upload_sidecars_leave_frozen_inputs_unchanged(tmp_path: Path) -> None:
    workflow = (ROOT / "examples/planned-wheels/ci.yml").read_text()
    match = re.search(
        r"(?m)^      - name: Stage verified distributions for PyPI\n"
        r"(?:(?:^        .*\n)|(?:^\n))*?^        run: \|\n"
        r"((?:^          .*\n|^\n)+)",
        workflow,
    )
    assert match is not None, "The uploader needs a separate verified input copy"
    stage = workflow.split("      - name: Stage verified distributions for PyPI\n", 1)[
        1
    ].split("\n      - ", 1)[0]
    assert "if: steps.upload.outputs.pypi_upload == 'true'" in stage
    assert "working-directory: ${{ github.workspace }}" in stage
    assert workflow.index("phase: check-upload") < workflow.index(
        "Stage verified distributions for PyPI"
    )
    uploader = workflow.split("uses: pypa/gh-action-pypi-publish@", 1)[1].split(
        "\n      - ", 1
    )[0]
    assert "packages-dir: pypi-dist/" in uploader
    script = textwrap.dedent(match.group(1))
    original = tmp_path / "dist"
    original.mkdir()
    frozen = {
        "example-1.0-py3-none-any.whl": b"tested wheel",
        "example-1.0.tar.gz": b"tested sdist",
    }
    for name, data in frozen.items():
        (original / name).write_bytes(data)
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_WORKSPACE": str(tmp_path)},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    staging = tmp_path / "pypi-dist"
    assert {p.name: p.read_bytes() for p in staging.iterdir()} == frozen
    for name in frozen:
        (staging / (name + ".publish.attestation")).write_bytes(
            b"generated PyPI sidecar"
        )
    assert {p.name: p.read_bytes() for p in original.iterdir()} == frozen
    before_retry = {p.name: p.read_bytes() for p in staging.iterdir()}
    retry = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_WORKSPACE": str(tmp_path)},
        capture_output=True,
        check=False,
    )
    assert retry.returncode != 0
    assert {p.name: p.read_bytes() for p in staging.iterdir()} == before_retry
    assert {p.name: p.read_bytes() for p in original.iterdir()} == frozen
