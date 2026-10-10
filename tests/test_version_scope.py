"""version_scope must report artifact-neutral release bumps and only those."""

import subprocess
from pathlib import Path

import pytest

from release_ci.version_scope import (
    anchors,
    own_version_hunks_only,
    version_only_change,
)

PKG = "example-pkg"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    files = {
        "CHANGELOG.md": "# Changelog\n",
        ".release-please-manifest.json": '{"." : "0.1.0"}',
        "pyproject.toml": '[project]\nname = "example-pkg"\nversion = "0.1.0"\n',
        "uv.lock": '[[package]]\nname = "example-pkg"\nversion = "0.1.0"\n',
        "src/pkg.py": '__version__ = "0.1.0"\n',
    }
    for name, value in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    _commit(tmp_path, "base")
    return tmp_path


def _commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message],
        cwd=repo,
        check=True,
    )


def rewrite(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


MANIFESTS = ("pyproject.toml", "uv.lock")


def test_release_prose_and_own_versions_skip(repo: Path) -> None:
    rewrite(repo, "CHANGELOG.md", "# Changelog\n## 0.2.0\n")
    rewrite(
        repo, "pyproject.toml", '[project]\nname = "example-pkg"\nversion = "0.2.0"\n'
    )
    rewrite(repo, "uv.lock", '[[package]]\nname = "example-pkg"\nversion = "0.2.0"\n')
    _commit(repo, "release 0.2.0")
    assert (
        version_only_change(
            repo,
            base="HEAD^",
            head="HEAD",
            package_name=PKG,
            versioned_manifests=MANIFESTS,
        )
        is True
    )


def test_renovate_lock_bump_still_builds(repo: Path) -> None:
    rewrite(
        repo,
        "uv.lock",
        '[[package]]\nname = "example-pkg"\nversion = "0.1.0"\n'
        '[[package]]\nname = "other"\nversion = "9.9.9"\n',
    )
    _commit(repo, "lock maintenance")
    assert (
        version_only_change(
            repo,
            base="HEAD^",
            head="HEAD",
            package_name=PKG,
            versioned_manifests=MANIFESTS,
        )
        is False
    )


def test_dependency_addition_to_pyproject_still_builds(repo: Path) -> None:
    rewrite(
        repo,
        "pyproject.toml",
        '[project]\nname = "example-pkg"\nversion = "0.1.0"\n'
        'dependencies = ["numpy>=2.0"]\n',
    )
    _commit(repo, "add dependency")
    assert (
        version_only_change(
            repo,
            base="HEAD^",
            head="HEAD",
            package_name=PKG,
            versioned_manifests=MANIFESTS,
        )
        is False
    )


def test_source_change_still_builds(repo: Path) -> None:
    rewrite(repo, "src/pkg.py", '__version__ = "0.1.0"\n# changed\n')
    _commit(repo, "code")
    assert (
        version_only_change(
            repo,
            base="HEAD^",
            head="HEAD",
            package_name=PKG,
            versioned_manifests=MANIFESTS,
        )
        is False
    )


def test_prose_only_change_skips(repo: Path) -> None:
    rewrite(repo, "CHANGELOG.md", "# Changelog\nnote\n")
    _commit(repo, "docs")
    assert (
        version_only_change(
            repo,
            base="HEAD^",
            head="HEAD",
            package_name=PKG,
            versioned_manifests=MANIFESTS,
        )
        is True
    )


def test_cargo_hunk_heading_anchor() -> None:
    patch = (
        "diff --git a/Cargo.lock b/Cargo.lock\n"
        '@@ -3 +3 @@ name="example-pkg"\n'
        '-version = "0.1.0"\n'
        '+version = "0.2.0"\n'
    )
    assert own_version_hunks_only(patch, anchors(PKG)) is True
    foreign = patch.replace('name="example-pkg"', 'name="other-pkg"')
    assert own_version_hunks_only(foreign, anchors(PKG)) is False


def test_fails_open_without_git_history(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    assert (
        version_only_change(
            tmp_path,
            base="HEAD^",
            head="HEAD",
            package_name=PKG,
            versioned_manifests=MANIFESTS,
        )
        is False
    )


def test_empty_diff_fails_open(repo: Path) -> None:
    assert (
        version_only_change(
            repo,
            base="HEAD",
            head="HEAD",
            package_name=PKG,
            versioned_manifests=MANIFESTS,
        )
        is False
    )
