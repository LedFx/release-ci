"""Disposable source configurations based on the canonical native example."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "tests/fixtures/planning/ledfx-senders"


def fleet_project(name: str) -> Path:
    if name in ("ledfx", "audio-hotplug"):
        return ROOT / "examples" / name
    return (
        ROOT
        / "tests/fixtures/planning"
        / ("python-samplerate-ledfx" if name == "samplerate-ledfx" else name)
    )


def write_project(
    directory: Path,
    *,
    pure: bool = False,
    version: str = "0.3.0",
    settings: dict[str, object] | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    text = (PROJECT / "pyproject.toml").read_text()
    text = re.sub(
        r'(?m)^version = "[^"]+"$', "version = " + json.dumps(version), text, count=1
    )
    if pure:
        text = text.split("[[tool.release-ci.targets]]", 1)[0]
    if settings:
        release = "\n[tool.release-ci]\n"
        images = settings.get("oci", [])
        for key, value in settings.items():
            if key != "oci":
                release += key + " = " + json.dumps(value) + "\n"
        if not isinstance(images, list):
            raise ValueError("Expected image list")
        for image in images:
            if not isinstance(image, dict):
                raise TypeError("Expected image settings")
            release += "\n[[tool.release-ci.oci]]\n"
            for key, value in image.items():
                release += str(key) + " = " + json.dumps(value) + "\n"
        text = (
            text.replace(
                "[[tool.release-ci.targets]]",
                release + "\n[[tool.release-ci.targets]]",
                1,
            )
            if not pure
            else text + release
        )
    (directory / "pyproject.toml").write_text(text)
    return directory
