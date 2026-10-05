"""Independent filenames and archive metadata checks before credentials write."""

import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

from .common import Hashes, PublicationError, digest
from .config import Config, Version, canonical, expand_tag, render
from .targets import WheelPlan


def files(directory: Path | None, expected: set[str]) -> Hashes:
    if directory is None:
        if expected:
            raise PublicationError("Artifact directory required")
        return {}
    if directory.is_symlink() or not directory.is_dir():
        raise PublicationError("Artifact root must be a regular directory")
    paths = list(directory.iterdir())
    if {p.name for p in paths} != expected or any(
        p.is_symlink() or not p.is_file() for p in paths
    ):
        raise PublicationError("Artifact file set differs from config")
    return {p.name: digest(p) for p in paths}


def validate_archive(path: Path, config: Config, version: Version) -> None:
    try:
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as wheel:
                metadata_names = [
                    n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")
                ]
                if len(metadata_names) != 1:
                    raise PublicationError("Wheel metadata missing or ambiguous")
                name = metadata_names[0]
                metadata = wheel.read(name).decode()
                info = Parser().parsestr(
                    wheel.read(name.removesuffix("METADATA") + "WHEEL").decode()
                )
                expected = expand_tag(path.name.removesuffix(".whl").split("-", 2)[2])
                actual: set[str] = set()
                for tag in info.get_all("Tag", []):
                    expanded = expand_tag(tag)
                    if actual & expanded:
                        raise PublicationError("Duplicate wheel metadata tag")
                    actual.update(expanded)
                if actual != expected:
                    raise PublicationError("Wheel tags differ from filename")
        else:
            with tarfile.open(path, "r:gz") as archive:
                names = [
                    m
                    for m in archive.getmembers()
                    if m.name == path.name.removesuffix(".tar.gz") + "/PKG-INFO"
                    and m.isfile()
                ]
                if len(names) != 1:
                    raise PublicationError("Sdist metadata missing or ambiguous")
                handle = archive.extractfile(names[0])
                if handle is None:
                    raise PublicationError("Missing sdist metadata")
                with handle:
                    metadata = handle.read().decode()
        parsed = Parser().parsestr(metadata)
        project_names: list[str] = parsed.get_all("Name") or []
        if (
            len(project_names) != 1
            or canonical(project_names[0]) != canonical(config.project)
            or parsed.get_all("Version") != [version.package]
        ):
            raise PublicationError("Distribution name/version differs from tag")
    except (
        OSError,
        ValueError,
        KeyError,
        UnicodeError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ):
        raise PublicationError("Invalid distribution archive") from None


def distributions(
    directory: Path, config: Config, version: Version, plan: WheelPlan
) -> Hashes:
    if plan.wheel_targets != config.wheel_targets:
        raise PublicationError("Wheel plan kind differs from config wheel_targets")
    if directory.is_symlink() or not directory.is_dir():
        raise PublicationError("Artifact root must be a regular directory")
    names = {path.name for path in directory.iterdir()}
    prefix = f"{config.wheel_stem}-{version.package}-"
    sdist = render(config.sdist, version)
    wheel_tags: dict[str, str] = {}
    for name in names - {sdist}:
        if not name.startswith(prefix) or not name.endswith(".whl"):
            raise PublicationError("Unexpected distribution filename")
        wheel_tags[name] = name[len(prefix) : -4]
    result = files(directory, set(wheel_tags) | {sdist})
    plan.coverage(wheel_tags)
    if names & config.asset_names(version):
        raise PublicationError("Explicit GitHub assets overlap Python distributions")
    for name in result:
        validate_archive(directory / name, config, version)
    return result
