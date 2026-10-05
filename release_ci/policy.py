"""Strict declarative policy; no executable consumer callbacks or URL overrides."""

import itertools
import re
from dataclasses import dataclass
from pathlib import Path

from .common import PublicationError, array, decode, flag, string, table


@dataclass(frozen=True)
class Version:
    tag_version: str
    package: str
    stable: tuple[int, int, int] | None


def parse_version(tag: str, prefix: str) -> Version:
    if not tag.startswith(prefix):
        raise PublicationError("Tag prefix differs from policy")
    value = tag[len(prefix) :]
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
        raise PublicationError("Missing or unknown policy keys")
    return obj


def strings(value: object) -> tuple[str, ...]:
    result = tuple(string(item) for item in array(value))
    if len(set(result)) != len(result):
        raise PublicationError("Duplicate policy entries")
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


@dataclass(frozen=True)
class Policy:
    repository: str
    workflow: str
    tag_prefix: str
    project: str
    wheel_stem: str
    wheel_tags: tuple[str, ...]
    sdist: str
    github_distributions: bool
    asset_templates: tuple[str, ...]
    oci: tuple[Image, ...]

    @classmethod
    def load(cls, path: Path) -> "Policy":
        if path.is_symlink() or not path.is_file():
            raise PublicationError("Policy must be a regular file")
        obj = keys(
            decode(path.read_bytes()),
            {
                "schema_version",
                "repository",
                "workflow",
                "tag_prefix",
                "python",
                "github_assets",
                "oci",
            },
        )
        if type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
            raise PublicationError("Unsupported policy schema")
        repository, workflow, prefix = (
            string(obj[k]) for k in ("repository", "workflow", "tag_prefix")
        )
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            or not re.fullmatch(r"\.github/workflows/[A-Za-z0-9_-]+\.ya?ml", workflow)
            or prefix not in ("", "v")
        ):
            raise PublicationError("Invalid repository/workflow/tag prefix")
        py = keys(obj["python"], {"project", "wheel_stem", "wheel_tags", "sdist"})
        project, stem, sdist = (
            string(py[k]) for k in ("project", "wheel_stem", "sdist")
        )
        if (
            not re.fullmatch(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*", project)
            or not re.fullmatch(r"[A-Za-z0-9_]+", stem)
            or canonical(stem) != canonical(project)
        ):
            raise PublicationError("Invalid Python project/stem")
        tags = strings(py["wheel_tags"])
        if not tags:
            raise PublicationError("At least one wheel is required")
        for tag in tags:
            expand_tag(tag)
        sample = parse_version("v1.2.3", "v")
        if "{version}" not in sdist or not render(sdist, sample).endswith(".tar.gz"):
            raise PublicationError("Versioned source archive required")
        gh = keys(obj["github_assets"], {"distributions", "files"})
        templates = strings(gh["files"])
        for template in templates:
            render(template, sample)
            if "{version}" not in template and "{tag_version}" not in template:
                raise PublicationError("Versioned release assets required")
        images: list[Image] = []
        for value in array(obj["oci"]):
            entry = keys(value, {"image", "platforms", "version_tag", "promote_latest"})
            image, version_tag = string(entry["image"]), string(entry["version_tag"])
            platforms = strings(entry["platforms"])
            if (
                not re.fullmatch(
                    r"(?:ghcr\.io|docker\.io)/[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*",
                    image,
                )
                or not platforms
                or any(p not in ("linux/amd64", "linux/arm64") for p in platforms)
                or version_tag != "{tag_version}"
            ):
                raise PublicationError("Unsupported OCI image/platform/tag policy")
            images.append(
                Image(image, platforms, version_tag, flag(entry["promote_latest"]))
            )
        if len({i.image for i in images}) != len(images):
            raise PublicationError("Duplicate OCI image")
        return cls(
            repository,
            workflow,
            prefix,
            project,
            stem,
            tags,
            sdist,
            flag(gh["distributions"]),
            templates,
            tuple(images),
        )

    def distribution_names(self, version: Version) -> set[str]:
        names = {
            f"{self.wheel_stem}-{version.package}-{tag}.whl" for tag in self.wheel_tags
        } | {render(self.sdist, version)}
        if names & self.asset_names(version):
            raise PublicationError(
                "Explicit GitHub assets overlap Python distributions"
            )
        return names

    def asset_names(self, version: Version) -> set[str]:
        names = {render(template, version) for template in self.asset_templates}
        if len(names) != len(self.asset_templates):
            raise PublicationError("Colliding asset templates")
        return names


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()
