"""CPython target coverage, independent of repaired deployment floors."""

import re
from dataclasses import dataclass
from typing import Literal

from .common import PublicationError, string
from .config import expand_tag, keys, strings

ARCHES = {
    "linux": ("x86_64", "aarch64", "armv7l", "i686"),
    "macos": ("x86_64", "arm64"),
    "windows": ("AMD64", "ARM64", "x86"),
}


def interpreter(value: str) -> tuple[int, bool]:
    match = re.fullmatch(r"cp3([1-9]\d?)(t?)", value)
    if match is None or int(match[1]) < 8 or (match[2] and int(match[1]) < 13):
        raise PublicationError(f"Unsupported CPython interpreter: {value}")
    return int(match[1]), bool(match[2])


def platform(value: str, *, repaired: bool) -> tuple[str, str]:
    if value in ("win_amd64", "win_arm64", "win32"):
        return "windows", value
    if not repaired:
        match = re.fullmatch(r"(manylinux|musllinux|macosx)_(.+)", value)
    else:
        match = re.fullmatch(
            r"(manylinux)(?:1|2010|2014|_2_[1-9]\d*)_(.+)", value
        ) or re.fullmatch(r"(musllinux)_1_[1-9]\d*_(.+)", value)
        if match is None:
            match = re.fullmatch(r"(macosx)_(?:1[0-9]|[2-9][0-9])_\d+_(.+)", value)
    if match is not None:
        family, arch = match.group(1, 2)
        if (
            repaired
            and value.startswith(("manylinux1_", "manylinux2010_"))
            and arch not in ("x86_64", "i686")
        ):
            raise PublicationError(f"Unsupported wheel platform: {value}")
        if arch in ARCHES["macos" if family == "macosx" else "linux"]:
            return family, arch
    raise PublicationError(f"Unsupported wheel platform: {value}")


@dataclass(frozen=True)
class Target:
    identifier: str
    minor: int
    threaded: bool
    family: str
    arch: str

    @classmethod
    def parse(cls, value: str) -> "Target":
        parts = value.split("-")
        if len(parts) != 2:
            raise PublicationError(f"Invalid build identifier: {value}")
        minor, threaded = interpreter(parts[0])
        family, arch = platform(parts[1], repaired=False)
        return cls(value, minor, threaded, family, arch)


@dataclass(frozen=True)
class WheelPlan:
    source_sha: str
    wheel_targets: Literal["pure", "cibuildwheel"]
    cibuildwheel: str | None
    targets: tuple[Target, ...]
    pyproject_sha256: str | None

    @classmethod
    def pure(cls, source_sha: str) -> "WheelPlan":
        if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
            raise PublicationError("Invalid pure wheel plan source SHA")
        return cls(source_sha, "pure", None, (), None)

    @classmethod
    def load(cls, value: object, source_sha: str) -> "WheelPlan":
        try:
            obj = keys(
                value,
                {
                    "schema_version",
                    "source_sha",
                    "wheel_targets",
                    "cibuildwheel",
                    "pyproject_sha256",
                    "targets",
                },
            )
        except PublicationError:
            raise PublicationError(
                "Invalid wheel plan: expected schema_version, source_sha, pyproject_sha256, wheel_targets, cibuildwheel and targets"
            ) from None
        if obj["wheel_targets"] != "cibuildwheel":
            raise PublicationError("Pre-build wheel plan must use cibuildwheel targets")
        if type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
            raise PublicationError("Unsupported wheel plan schema")
        sha, version = string(obj["source_sha"]), string(obj["cibuildwheel"])
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or sha != source_sha:
            raise PublicationError("Wheel plan source SHA differs from tested source")
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", version):
            raise PublicationError("Invalid cibuildwheel version in plan")
        targets = tuple(Target.parse(item) for item in strings(obj["targets"]))
        if not targets:
            raise PublicationError("Wheel plan has no targets")
        config_digest = string(obj["pyproject_sha256"])
        if not re.fullmatch(r"[0-9a-f]{64}", config_digest):
            raise PublicationError("Invalid pyproject digest in wheel plan")
        return cls(sha, "cibuildwheel", version, targets, config_digest)

    def value(self) -> dict[str, object]:
        if self.wheel_targets == "pure":
            return {
                "schema_version": 1,
                "source_sha": self.source_sha,
                "wheel_targets": "pure",
                "targets": ["py3-none-any"],
            }
        return {
            "schema_version": 1,
            "wheel_targets": "cibuildwheel",
            "source_sha": self.source_sha,
            "cibuildwheel": self.cibuildwheel,
            "pyproject_sha256": self.pyproject_sha256,
            "targets": [target.identifier for target in self.targets],
        }

    def validate_rows(self, rows: tuple[tuple[str, str], ...]) -> None:
        expected = {
            (
                host,
                {"AMD64": "win_amd64", "ARM64": "win_arm64", "x86": "win32"}.get(
                    arch, arch
                ),
            )
            for host, arch in rows
        }
        actual = {
            (
                {
                    "manylinux": "linux",
                    "musllinux": "linux",
                    "macosx": "macos",
                    "windows": "windows",
                }[target.family],
                target.arch,
            )
            for target in self.targets
        }
        if actual != expected:
            raise PublicationError(
                "Wheel plan platforms/architectures differ from configured target rows"
            )

    def coverage(self, wheel_tags: dict[str, str]) -> None:
        if self.wheel_targets == "pure":
            if not wheel_tags:
                raise PublicationError("Missing wheel targets: py3-none-any")
            if (
                len(wheel_tags) != 1
                or next(iter(wheel_tags.values())) != "py3-none-any"
            ):
                raise PublicationError(
                    "Pure wheel target requires exactly one py3-none-any wheel"
                )
            return
        covered: dict[str, str] = {}
        for filename, tags in sorted(wheel_tags.items()):
            expanded = [tag.split("-") for tag in sorted(expand_tag(tags))]
            shapes = {(py, abi) for py, abi, _ in expanded}
            platforms = {platform(p, repaired=True) for _, _, p in expanded}
            if len(shapes) != 1 or len(platforms) != 1:
                raise PublicationError(
                    f"Mixed wheel interpreter/ABI/platform: {filename}"
                )
            py, abi = shapes.pop()
            minor, threaded = interpreter(py)
            if threaded or abi not in (py, py + "t", "abi3"):
                raise PublicationError(f"Invalid wheel interpreter/ABI: {filename}")
            family, arch = platforms.pop()
            candidates = [
                target
                for target in self.targets
                if (target.family, target.arch) == (family, arch)
            ]
            if abi == "abi3":
                if not any(t.minor == minor and not t.threaded for t in candidates):
                    raise PublicationError(f"ABI3 baseline is not selected: {filename}")
                matches = [t for t in candidates if t.minor >= minor and not t.threaded]
            else:
                matches = [
                    t
                    for t in candidates
                    if (t.minor, t.threaded) == (minor, abi == py + "t")
                ]
            if not matches:
                raise PublicationError(
                    f"Unexpected wheel has no planned target: {filename}"
                )
            for target in matches:
                if target.identifier in covered:
                    raise PublicationError(
                        f"Overlapping target {target.identifier}: {covered[target.identifier]}, {filename}"
                    )
                covered[target.identifier] = filename
        missing = sorted(
            t.identifier for t in self.targets if t.identifier not in covered
        )
        if missing:
            raise PublicationError("Missing wheel targets: " + ", ".join(missing))
