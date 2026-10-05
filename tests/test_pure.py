"""Pure targets share archive validation and immutable transaction identity."""

import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from release_ci.archives import distributions
from release_ci.common import PublicationError, decode, table
from release_ci.policy import Policy, Version, parse_version, render
from release_ci.publisher import Publisher
from release_ci.targets import WheelPlan
from tests.test_transaction import SHA, native_plan

POLICY = Path(__file__).resolve().parents[1] / "examples/audio-hotplug.json"


def pure_archives(directory: Path, policy: Policy, version: Version) -> None:
    metadata = (
        f"Metadata-Version: 2.4\nName: {policy.project}\nVersion: {version.package}\n"
    )
    stem = f"{policy.wheel_stem}-{version.package}"
    with zipfile.ZipFile(directory / (stem + "-py3-none-any.whl"), "w") as wheel:
        wheel.writestr(stem + ".dist-info/METADATA", metadata)
        wheel.writestr(
            stem + ".dist-info/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n"
        )
    name = render(policy.sdist, version)
    with tarfile.open(directory / name, "w:gz") as archive:
        member = tarfile.TarInfo(name.removesuffix(".tar.gz") + "/PKG-INFO")
        content = metadata.encode()
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))


@pytest.mark.parametrize(
    "change", [None, "missing", "extra", "native", "malformed", "sdist", "mixed_plan"]
)
def test_pure_archive_coverage(tmp_path: Path, change: str | None) -> None:
    policy = Policy.load(POLICY)
    version = parse_version("v1.2.3", "v")
    pure_archives(tmp_path, policy, version)
    wheel = next(tmp_path.glob("*.whl"))
    if change == "missing":
        wheel.unlink()
    elif change == "sdist":
        next(tmp_path.glob("*.tar.gz")).unlink()
    elif change not in (None, "mixed_plan"):
        replacement = wheel.name.replace(
            "py3-none-any",
            "cp311-cp311-win_amd64" if change in ("extra", "native") else "bad",
        )
        if change == "extra":
            (tmp_path / replacement).write_bytes(wheel.read_bytes())
        else:
            wheel.rename(tmp_path / replacement)
    effective = (
        WheelPlan.load(decode(native_plan()), SHA)
        if change == "mixed_plan"
        else WheelPlan.pure(SHA)
    )
    if change is None:
        assert len(distributions(tmp_path, policy, version, WheelPlan.pure(SHA))) == 2
    else:
        with pytest.raises(PublicationError):
            distributions(tmp_path, policy, version, effective)


@pytest.mark.parametrize(
    "kind,serialized",
    [
        ("pure", native_plan()),
        ("pure", "{}"),
        ("cibuildwheel", None),
        ("cibuildwheel", "{"),
        ("cibuildwheel", json.dumps(WheelPlan.pure(SHA).value())),
    ],
)
def test_inappropriate_or_missing_plans_fail_before_commands(
    tmp_path: Path, kind: str, serialized: str | None
) -> None:
    value = table(decode(POLICY.read_bytes()))
    table(value["python"])["wheel_targets"] = kind
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(value))
    with pytest.raises(PublicationError):
        Publisher(
            tmp_path,
            tmp_path / "snapshot",
            "LedFx/audio-hotplug",
            "v1.2.3",
            SHA,
            policy=policy,
            wheel_plan=serialized,
        )


def test_pure_plan_has_source_identity_without_cibuildwheel_version() -> None:
    assert WheelPlan.pure(SHA).value() == {
        "schema_version": 1,
        "source_sha": SHA,
        "wheel_targets": "pure",
        "targets": ["py3-none-any"],
    }
    with pytest.raises(PublicationError, match="source SHA"):
        WheelPlan.pure("invalid")
