"""Strict declarative config; no executable consumer callbacks or URL overrides."""

import itertools
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .common import PublicationError, array, flag, string, table


@dataclass(frozen=True)
class Version:
    tag_version: str
    package: str
    stable: tuple[int, int, int] | None


def parse_version(tag: str) -> Version:
    value = tag.removeprefix("v")
    match = re.fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:(a|b|rc)(0|[1-9]\d*)|-(alpha|beta|rc)\.(0|[1-9]\d*))?",
        value,
    )
    if match is None:
        raise PublicationError("Unsupported release version")
    suffix = match[4] or {"alpha": "a", "beta": "b", "rc": "rc"}.get(match[6], "")
    package = ".".join(match.group(1, 2, 3)) + suffix + (match[5] or match[7] or "")
    stable = (int(match[1]), int(match[2]), int(match[3])) if not suffix else None
    return Version(value, package, stable)


def expand_tag(value: str) -> set[str]:
    if not re.fullmatch(
        r"[a-z0-9_]+(?:\.[a-z0-9_]+)*-[a-z0-9_]+(?:\.[a-z0-9_]+)*-[a-z0-9_]+(?:\.[a-z0-9_]+)*",
        value,
    ):
        raise PublicationError("Invalid wheel tag")
    return {
        "-".join(parts)
        for parts in itertools.product(*(part.split(".") for part in value.split("-")))
    }


def keys(value: object, expected: set[str]) -> dict[str, object]:
    obj = table(value)
    if set(obj) != expected:
        raise PublicationError("Missing or unknown config keys")
    return obj


def strings(value: object) -> tuple[str, ...]:
    result = tuple(string(item) for item in array(value))
    if len(set(result)) != len(result):
        raise PublicationError("Duplicate config entries")
    return result


def render(template: str, version: Version) -> str:
    value = template.replace("{version}", version.package).replace(
        "{tag_version}", version.tag_version
    )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or ".." in value:
        raise PublicationError("Invalid filename template")
    return value


@dataclass(frozen=True)
class Image:
    image: str
    platforms: tuple[str, ...]
    version_tag: str
    promote_latest: bool


RELEASE_KEYS = {"targets", "github-distributions", "assets", "oci"}


def read_project(project: Path) -> dict[str, object]:
    path = project / "pyproject.toml"
    if project.is_symlink() or path.is_symlink() or not path.is_file():
        raise PublicationError("Configuration must be a regular pyproject.toml")
    try:
        return table(tomllib.loads(path.read_text()))
    except (OSError, ValueError):
        raise PublicationError("Invalid or unavailable pyproject.toml") from None


def release_settings(document: dict[str, object]) -> dict[str, object]:
    settings = table(table(document.get("tool", {})).get("release-ci", {}))
    if set(settings) - RELEASE_KEYS:
        raise PublicationError("Unknown tool.release-ci settings")
    return settings


def target_rows(settings: dict[str, object]) -> tuple[dict[str, str], ...]:
    from .targets import ARCHES

    if "targets" not in settings:
        return ()
    rows = array(settings["targets"])
    if not rows:
        raise PublicationError("Native targets must contain at least one build row")
    result: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
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
        if (host, arch) in identities:
            raise PublicationError("Duplicate build row")
        identities.add((host, arch))
        result.append(row)
    return tuple(result)


def caller_workflow(repository: str, workflow_ref: str, tag: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise PublicationError("Invalid canonical repository")
    match = re.fullmatch(
        re.escape(repository)
        + r"/(\.github/workflows/[A-Za-z0-9_-]+\.ya?ml)@"
        + re.escape("refs/tags/" + tag),
        workflow_ref,
    )
    if match is None:
        raise PublicationError(
            "Caller workflow must match repository and canonical tag ref"
        )
    parse_version(tag)
    return match[1]


@dataclass(frozen=True)
class Config:
    project: str
    wheel_stem: str
    wheel_targets: Literal["pure", "cibuildwheel"]
    sdist: str
    github_distributions: bool
    asset_templates: tuple[str, ...]
    oci: tuple[Image, ...]
    static_version: str | None
    build_rows: tuple[tuple[str, str], ...]

    @classmethod
    def load(cls, project_dir: Path) -> "Config":
        document = read_project(project_dir)
        metadata = table(document.get("project"))
        project = string(metadata.get("name"))
        if not re.fullmatch(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*", project):
            raise PublicationError("Invalid Python project name")
        dynamic = strings(metadata.get("dynamic", []))
        if "version" in metadata and "version" in dynamic:
            raise PublicationError("Project version cannot be both static and dynamic")
        static = string(metadata["version"]) if "version" in metadata else None
        if static is None and "version" not in dynamic:
            raise PublicationError(
                "Project requires static or dynamic version metadata"
            )
        if static is not None:
            parse_version(static)
        settings = release_settings(document)
        rows = target_rows(settings)
        stem = canonical(project).replace("-", "_")
        sample = parse_version("v1.2.3")
        templates = strings(settings.get("assets", []))
        for template in templates:
            render(template, sample)
            if "{version}" not in template and "{tag_version}" not in template:
                raise PublicationError("Versioned release assets required")
        images: list[Image] = []
        for value in array(settings.get("oci", [])):
            entry = table(value)
            if set(entry) - {"image", "platforms", "promote-latest"} or not {
                "image",
                "platforms",
            }.issubset(entry):
                raise PublicationError("Missing or unknown OCI settings")
            image = string(entry["image"])
            platforms = strings(entry["platforms"])
            if (
                not re.fullmatch(
                    r"(?:ghcr\.io|docker\.io)/[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*",
                    image,
                )
                or not platforms
                or any(p not in ("linux/amd64", "linux/arm64") for p in platforms)
            ):
                raise PublicationError("Unsupported OCI image/platform settings")
            images.append(
                Image(
                    image,
                    platforms,
                    "{tag_version}",
                    flag(entry.get("promote-latest", False)),
                )
            )
        if len({i.image for i in images}) != len(images):
            raise PublicationError("Duplicate OCI image")
        return cls(
            project,
            stem,
            "cibuildwheel" if rows else "pure",
            stem + "-{version}.tar.gz",
            flag(settings.get("github-distributions", True)),
            templates,
            tuple(images),
            static,
            tuple((row["platform"], row["arch"]) for row in rows),
        )

    def asset_names(self, version: Version) -> set[str]:
        names = {render(template, version) for template in self.asset_templates}
        if len(names) != len(self.asset_templates):
            raise PublicationError("Colliding asset templates")
        return names


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()
