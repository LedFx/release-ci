"""Optional immutable OCI indexes; package-only callers do no registry work."""

import re
from collections.abc import Callable, Mapping
from pathlib import Path

from .common import (
    Command,
    MissingArtifact,
    PublicationError,
    array,
    decode,
    flag,
    string,
    table,
)
from .config import Image, Version

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
Children = dict[str, dict[str, str]]
Guard = Callable[[], Mapping[str, object]]


class OCI:
    def __init__(
        self,
        images: tuple[Image, ...],
        directory: Path | None,
        version: Version,
        sha: str,
        command: Command,
    ) -> None:
        self.images, self.directory, self.version, self.sha, self.command = (
            images,
            directory,
            version,
            sha,
            command,
        )

    def sources(self) -> Children:
        if not self.images:
            if self.directory is not None:
                raise PublicationError("OCI directory supplied without OCI config")
            return {}
        directory = self.directory
        if directory is None or directory.is_symlink() or not directory.is_dir():
            raise PublicationError("OCI receipt directory required")
        architectures = {
            p.split("/")[1] for image in self.images for p in image.platforms
        }
        paths = list(directory.iterdir())
        if {p.name for p in paths} != {
            f"docker-{arch}.txt" for arch in architectures
        } or any(p.is_symlink() or not p.is_file() for p in paths):
            raise PublicationError("OCI receipt files differ from config")
        sources: Children = {image.image: {} for image in self.images}
        for arch in architectures:
            for ref in (directory / f"docker-{arch}.txt").read_text().split():
                image, separator, digest = ref.partition("@")
                if image.count("/") == 1:
                    image = "docker.io/" + image
                if (
                    image not in sources
                    or separator != "@"
                    or not DIGEST.fullmatch(digest)
                    or arch in sources[image]
                ):
                    raise PublicationError("Invalid/duplicate OCI source receipt")
                sources[image][arch] = f"{image}@{digest}"
        for image in self.images:
            if set(sources[image.image]) != {p.split("/")[1] for p in image.platforms}:
                raise PublicationError("OCI source platforms differ from config")
        return sources

    def inspect(
        self, ref: str, *options: str, allow_missing: bool = False
    ) -> dict[str, object] | None:
        try:
            return table(
                decode(
                    self.command(
                        ["docker", "buildx", "imagetools", "inspect", ref, *options]
                    )
                )
            )
        except MissingArtifact:
            if allow_missing:
                return None
            raise

    def required(self, ref: str, *options: str) -> dict[str, object]:
        result = self.inspect(ref, *options)
        if result is None:
            raise PublicationError("OCI artifact missing")
        return result

    def children(self, manifest: Mapping[str, object]) -> dict[str, str]:
        result: dict[str, str] = {}
        for value in array(manifest.get("manifests")):
            descriptor = table(value)
            platform = table(descriptor.get("platform"))
            digest = string(descriptor.get("digest"))
            if not DIGEST.fullmatch(digest):
                raise PublicationError("Invalid OCI descriptor digest")
            if platform.get("os") == platform.get("architecture") == "unknown":
                continue
            arch = string(platform.get("architecture"))
            if (
                platform.get("os") != "linux"
                or arch not in ("amd64", "arm64")
                or arch in result
            ):
                raise PublicationError("Unexpected OCI platform")
            result[arch] = digest
        if not result:
            raise PublicationError("OCI index has no runnable children")
        return result

    def source_children(self) -> Children:
        result: Children = {}
        for image, sources in self.sources().items():
            result[image] = {}
            for arch, ref in sources.items():
                raw = self.required(ref, "--raw")
                if "manifests" in raw:
                    children = self.children(raw)
                    if set(children) != {arch}:
                        raise PublicationError("OCI source index platform mismatch")
                    digest = children[arch]
                else:
                    config = self.required(ref, "--format", "{{json .Image}}")
                    if (
                        config.get("os") != "linux"
                        or config.get("architecture") != arch
                    ):
                        raise PublicationError("OCI source platform mismatch")
                    digest = ref.split("@", 1)[1]
                result[image][arch] = digest
        return result

    def checked_children(self, snapshot: Mapping[str, object]) -> Children:
        children = self.source_children()
        if children != snapshot.get("oci_children"):
            raise PublicationError("OCI sources changed from snapshot")
        return children

    def verify(self, children: Children, *, complete: bool = False) -> list[str]:
        missing: list[str] = []
        for image, expected in children.items():
            for tag in (self.version.tag_version, self.sha):
                ref = f"{image}:{tag}"
                found = self.inspect(ref, "--raw", allow_missing=not complete)
                if found is None:
                    missing.append(ref)
                elif self.children(found) != expected:
                    raise PublicationError(
                        "Immutable OCI tag conflicts with tested children"
                    )
        return missing

    def digests(self, children: Children) -> dict[str, str]:
        self.verify(children, complete=True)
        result: dict[str, str] = {}
        for image, expected in children.items():
            descriptor = self.required(
                f"{image}:{self.version.tag_version}", "--format", "{{json .Manifest}}"
            )
            digest = string(descriptor.get("digest"))
            if (
                not DIGEST.fullmatch(digest)
                or self.children(self.required(f"{image}@{digest}", "--raw"))
                != expected
            ):
                raise PublicationError(
                    "OCI index descriptor differs from tested children"
                )
            result[image] = digest
        return result

    def verify_attestations(
        self, children: Children, repo: str, workflow: str, tag: str
    ) -> dict[str, str]:
        digests = self.digests(children)
        for image, digest in digests.items():
            self.command(
                [
                    "gh",
                    "attestation",
                    "verify",
                    f"oci://{image}@{digest}",
                    "--repo",
                    repo,
                    "--signer-workflow",
                    f"{repo}/{workflow}",
                    "--source-digest",
                    self.sha,
                    "--source-ref",
                    f"refs/tags/{tag}",
                    "--deny-self-hosted-runners",
                ]
            )
        return digests

    def promote(self, children: Children, guard: Guard) -> dict[str, str]:
        for ref in self.verify(children):
            release = guard()
            if not flag(release["draft"]):
                break
            image = ref.rsplit(":", 1)[0]
            found = self.inspect(ref, "--raw", allow_missing=True)
            if found is not None:
                if self.children(found) != children[image]:
                    raise PublicationError(
                        "OCI version tag appeared with conflicting children"
                    )
                continue
            self.command(
                [
                    "docker",
                    "buildx",
                    "imagetools",
                    "create",
                    "--tag",
                    ref,
                    *(
                        f"{image}@{digest}"
                        for _, digest in sorted(children[image].items())
                    ),
                ]
            )
        return self.digests(children)

    def promote_latest(
        self,
        children: Children,
        digests: dict[str, str],
        guard: Guard,
        eligible: Callable[[], bool],
    ) -> None:
        if self.digests(children) != digests:
            raise PublicationError("OCI version descriptor changed after attestation")
        for image in self.images:
            if not image.promote_latest or not eligible() or not flag(guard()["draft"]):
                continue
            target = f"{image.image}:latest"
            digest = digests[image.image]
            self.command(
                [
                    "docker",
                    "buildx",
                    "imagetools",
                    "create",
                    "--tag",
                    target,
                    f"{image.image}@{digest}",
                ]
            )
            descriptor = self.required(target, "--format", "{{json .Manifest}}")
            if (
                descriptor.get("digest") != digest
                or self.children(self.required(target, "--raw"))
                != children[image.image]
            ):
                raise PublicationError("OCI latest promotion did not verify")
