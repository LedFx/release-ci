"""Detect release-metadata-only changes for CI planning.

Shared by every LedFx repository that releases through release-please. A
consumer declares its version-bearing manifests; this module decides whether
a change set can alter built artifacts at all:

- release prose (changelog, manifest, ``__version__`` literal): never;
- a manifest (pyproject.toml, Cargo.toml, lock files): only when every
  changed line rewrites the package's own version declaration, identified
  per hunk by the anchor git records beside the hunk range or the nearest
  preceding context line. A renovate dependency bump lands in another
  package's table and fails the anchor check, so it still builds.

Any ambiguity fails open: unknown history, unreadable diffs and empty
change sets all report "not version-only", and the full matrix runs.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

#: Changed paths that can never alter built artifacts on their own.
PROSE_METADATA = frozenset(
    {
        "CHANGELOG.md",
        "CHANGELOG.rst",
        ".release-please-manifest.json",
    }
)

#: Anchors that identify the package's own version table. Compared with all
#: whitespace removed, because hunk headings inherit the source line's format.
ANCHORS = ('name="ledfx-senders"',)  # replaced per consumer via anchors()


def anchors(package_name: str) -> tuple[str, ...]:
    """Anchor prefixes for one package's version tables across lock formats."""
    name = f'name="{package_name}"'
    return (name.replace(" ", ""), "[package]", "[project]")


def changed_files(root: Path, base: str, head: str) -> list[str] | None:
    """Changed paths between base and head, or None when git cannot answer."""
    try:
        listed = subprocess.run(
            ["git", "diff", "--name-only", base, head],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    return listed


def manifest_diff(root: Path, name: str, base: str, head: str) -> str:
    """Unified-zero diff of one file, or "" when git cannot answer."""
    try:
        return subprocess.run(
            ["git", "diff", "--unified=0", base, head, "--", name],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def own_version_hunks_only(patch: str, package_anchors: tuple[str, ...]) -> bool:
    """True when every hunk rewrites the package's own version declarations.

    Each hunk must touch only ``version`` values and sit inside the
    package's own table, identified by the hunk heading's trailing context
    (``@@ -3 +3 @@ name="pkg"``) or the nearest preceding context line.
    """
    if not patch:
        return False
    for hunk in re.split(r"^@@", patch, flags=re.MULTILINE)[1:]:
        lines = hunk.splitlines()
        parts = lines[0].split(maxsplit=3)
        anchor = parts[3].lstrip() if len(parts) > 3 else ""
        changed: list[str] = []
        for line in lines[1:]:
            body = line[1:].lstrip()
            if line[:1] in {"+", "-"}:
                changed.append(body)
            elif body.startswith(("name = ", "[package]", "[project]")):
                anchor = body
        if not changed or not all(c.startswith("version") for c in changed):
            return False
        compact = anchor.replace(" ", "")
        if not compact.startswith(package_anchors):
            return False
    return True


def version_only_change(
    root: Path,
    *,
    base: str,
    head: str,
    package_name: str,
    versioned_manifests: Iterable[str],
    prose_metadata: frozenset[str] = PROSE_METADATA,
) -> bool:
    """True when the whole diff is release metadata a rebuild cannot alter.

    ``base`` is caller-chosen: ``origin/main...HEAD`` for a PR merge ref,
    ``HEAD^`` for a direct push. Fails open on any git problem.
    """
    listed = changed_files(root, base, head)
    if not listed:
        return False
    manifests = tuple(
        dict.fromkeys(name for name in listed if name in set(versioned_manifests))
    )
    rest = set(listed) - set(manifests)
    if not manifests:
        return bool(rest) and rest <= prose_metadata
    if rest and not rest <= prose_metadata:
        return False
    package_anchors = anchors(package_name)
    return all(
        own_version_hunks_only(manifest_diff(root, name, base, head), package_anchors)
        for name in manifests
    )
