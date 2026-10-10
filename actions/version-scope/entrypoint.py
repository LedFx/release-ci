"""Standalone version-scope entrypoint for the composite action."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from release_ci.version_scope import version_only_change


def main() -> int:
    package_name = os.environ.get("PACKAGE_NAME", "").strip()
    if not package_name:
        print("package-name input is required", file=sys.stderr)
        return 1
    manifests = tuple(
        line.strip()
        for line in os.environ.get("VERSIONED_MANIFESTS", "").splitlines()
        if line.strip()
    )
    if not manifests:
        print("versioned-manifests input is required", file=sys.stderr)
        return 1
    prose = {
        "CHANGELOG.md",
        "CHANGELOG.rst",
        ".release-please-manifest.json",
        *(
            line.strip()
            for line in os.environ.get("PROSE_FILES", "").splitlines()
            if line.strip()
        ),
    }
    simple = {
        line.strip()
        for line in os.environ.get("SIMPLE_VERSION_FILES", "").splitlines()
        if line.strip()
    }
    base = os.environ.get("SCOPE_BASE", "")
    if not base:
        print(
            "version-scope runs only on pull_request and branch pushes", file=sys.stderr
        )
        return 0  # unset means the action chose to always build
    result = version_only_change(
        Path.cwd(),
        base=base,
        head="HEAD",
        package_name=package_name,
        versioned_manifests=manifests,
        prose_metadata=frozenset(prose),
        simple_version_files=frozenset(simple),
    )
    destination = os.environ.get("GITHUB_OUTPUT")
    if destination:
        with Path(destination).open("a") as handle:
            handle.write(f"version-only={str(result).lower()}\n")
    print(f"version-only={str(result).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
