"""Enumerate the locked cibuildwheel configuration before unprivileged builds."""

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path

from .common import PublicationError, array, string, table
from .policy import keys
from .targets import ARCHES, Target, WheelPlan


def plan(project: Path, source_sha: str) -> tuple[dict[str, object], WheelPlan]:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise PublicationError("Planning requires the tested source commit SHA")
    path = project / "pyproject.toml"
    if path.is_symlink() or not path.is_file():
        raise PublicationError("Build configuration must be a regular pyproject.toml")
    config = tomllib.loads(path.read_text())
    rows = array(
        keys(table(config.get("tool")).get("release-ci"), {"targets"})["targets"]
    )
    if not rows:
        raise PublicationError("At least one build row is required")
    selectors = ("BUILD", "SKIP", "ENABLE", "ARCHS", "PROJECT_REQUIRES_PYTHON")
    if any(
        k == "CIBW_" + option or k.startswith("CIBW_" + option + "_")
        for k in os.environ
        for option in selectors
    ):
        raise PublicationError(
            "Put cibuildwheel selectors in pyproject.toml, not CIBW_* environment variables"
        )
    # Config selectors are the sole authority. Do not inherit CIBW_BUILD, hooks,
    # platform overrides or a caller's other CIBW_* variables into enumeration.
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CIBW_")}
    matrix: list[dict[str, str]] = []
    identifiers: list[str] = []
    for value in rows:
        row = {k: string(v) for k, v in table(value).items()}
        if not {"platform", "arch", "runner"}.issubset(row) or any(
            not re.fullmatch(r"[a-z][a-z0-9_-]*", k) or not v or "\n" in v or "\r" in v
            for k, v in row.items()
        ):
            raise PublicationError(
                "Build rows require platform/arch/runner and string metadata"
            )
        host, arch = row["platform"], row["arch"]
        if host not in ARCHES or arch not in ARCHES[host]:
            raise PublicationError(
                "Build rows require a supported explicit platform/architecture"
            )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "cibuildwheel",
                ".",
                "--config-file",
                "pyproject.toml",
                "--platform",
                host,
                "--archs",
                arch,
                "--print-build-identifiers",
            ],
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()[:2000]
            raise PublicationError(
                f"cibuildwheel enumeration failed for {host}/{arch}: {detail}"
            )
        selected = result.stdout.splitlines()
        if not selected:
            raise PublicationError(f"Empty cibuildwheel selection for {host}/{arch}")
        for identifier in selected:
            target = Target.parse(identifier)
            expected_arch = {
                "AMD64": "win_amd64",
                "ARM64": "win_arm64",
                "x86": "win32",
            }.get(arch, arch)
            expected_families = {
                "linux": {"manylinux", "musllinux"},
                "macos": {"macosx"},
                "windows": {"windows"},
            }
            if (
                target.arch != expected_arch
                or target.family not in expected_families[host]
            ):
                raise PublicationError(
                    "Enumerated identifier differs from its build row"
                )
        identifiers.extend(selected)
        matrix.append(row)
    wheel_plan = WheelPlan.load(
        {
            "schema_version": 1,
            "source_sha": source_sha,
            "cibuildwheel": version("cibuildwheel"),
            "targets": identifiers,
        },
        source_sha,
    )
    return {"include": matrix}, wheel_plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if head.returncode or head.stdout.strip() != args.source_sha:
            raise PublicationError(
                "Planning checkout HEAD differs from tested source SHA"
            )
        matrix, wheel_plan = plan(Path.cwd(), args.source_sha)
        outputs = {"matrix": matrix, "wheel-plan": wheel_plan.value()}
        destination = os.environ.get("GITHUB_OUTPUT")
        if destination:
            with Path(destination).open("a") as handle:
                handle.writelines(
                    f"{key}={json.dumps(value, separators=(',', ':'))}\n"
                    for key, value in outputs.items()
                )
        print(json.dumps(outputs, indent=2))
    except (PublicationError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
