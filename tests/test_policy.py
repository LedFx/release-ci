"""Independent schema and package-tag boundaries."""

from pathlib import Path

import pytest

from release_ci.common import PublicationError, array
from release_ci.policy import expand_tag, parse_version


def test_compressed_wheel_tags_expand_cartesian_product() -> None:
    assert expand_tag("py2.py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64") == {
        "py2-none-manylinux_2_17_x86_64",
        "py3-none-manylinux_2_17_x86_64",
        "py2-none-manylinux2014_x86_64",
        "py3-none-manylinux2014_x86_64",
    }


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3rc1", "1.2.3-rc.1"])
def test_version_normalization(version: str) -> None:
    parsed = parse_version("v" + version, "v")
    assert parsed.package == version.replace("-rc.", "rc")


@pytest.mark.parametrize(
    "tag,placeholder,collides",
    [
        ("v1.2.3", "{version}", True),
        ("v1.2.3", "{tag_version}", True),
        ("v1.2.3rc1", "{tag_version}", True),
        ("v1.2.3-rc.1", "{version}", True),
        ("v1.2.3-rc.1", "{tag_version}", False),
    ],
)
def test_asset_collision_uses_actual_expanded_package_and_tag_versions(
    tmp_path: Path, tag: str, placeholder: str, collides: bool
) -> None:
    from dataclasses import replace

    from release_ci.policy import Policy

    policy = Policy.load(
        Path(__file__).resolve().parents[1] / "examples/audio-hotplug.json"
    )
    policy = replace(
        policy,
        github_distributions=False,
        asset_templates=(f"audio_hotplug-{placeholder}.tar.gz",),
    )
    version = parse_version(tag, "v")
    from release_ci.archives import distributions
    from release_ci.targets import WheelPlan
    from tests.test_pure import pure_archives

    pure_archives(tmp_path, policy, version)
    if collides:
        with pytest.raises(PublicationError, match="overlap"):
            distributions(tmp_path, policy, version, WheelPlan.pure("1" * 40))
    else:
        assert set(
            distributions(tmp_path, policy, version, WheelPlan.pure("1" * 40))
        ).isdisjoint(policy.asset_names(version))


@pytest.mark.parametrize(
    "version", ["1.2", "01.2.3", "1.2.3/dev", "1.2.3+local", "1.2.3\n"]
)
def test_unsupported_version_rejected(version: str) -> None:
    with pytest.raises(PublicationError):
        parse_version("v" + version, "v")


def test_every_fleet_policy_matches_independently_fetched_manifest() -> None:
    from release_ci.common import array, decode, string, table
    from release_ci.planner import plan
    from release_ci.policy import Policy, render
    from release_ci.targets import WheelPlan

    root = Path(__file__).resolve().parents[1]
    for fixture in (root / "tests/fixtures").glob("*.json"):
        if fixture.name.endswith("summary.json"):
            continue
        obj = table(decode(fixture.read_bytes()))
        policy = Policy.load(root / "examples" / fixture.name)
        version = parse_version("v" + string(obj["version"]), "v")
        names = {string(n) for n in array(obj["filenames"])}
        config = (
            "python-samplerate-ledfx"
            if fixture.stem == "samplerate-ledfx"
            else fixture.stem
        )
        expected = (
            WheelPlan.pure("1" * 40)
            if policy.wheel_targets == "pure"
            else plan(root / "tests/fixtures/planning" / config, "1" * 40)[1]
        )
        expected.coverage(
            {
                n: n.removesuffix(".whl").split("-", 2)[2]
                for n in names
                if n.endswith(".whl")
            }
        )
        assert names - {n for n in names if n.endswith(".whl")} == {
            render(policy.sdist, version)
        }


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown",
        "nested_unknown",
        "duplicate",
        "schema_bool",
        "template",
        "registry",
        "wheel_targets",
        "legacy",
        "mixed",
        "project",
        "duplicate_image",
    ],
)
def test_strict_policy_rejects_invalid_inputs(tmp_path: Path, mutation: str) -> None:
    import json
    from pathlib import Path

    from release_ci.common import decode, table
    from release_ci.policy import Policy

    root = Path(__file__).resolve().parents[1]
    obj = table(decode((root / "examples/ledfx.json").read_bytes()))
    if mutation == "unknown":
        obj["command"] = "publish anything"
    elif mutation == "nested_unknown":
        table(obj["python"])["skip_validation"] = True
    elif mutation == "schema_bool":
        obj["schema_version"] = True
    elif mutation == "template":
        table(obj["python"])["sdist"] = "../{version}.tar.gz"
    elif mutation == "registry":
        obj["pypi_url"] = "https://test.pypi.org"
    elif mutation == "wheel_targets":
        table(obj["python"])["wheel_targets"] = "auto"
    elif mutation == "legacy":
        obj["schema_version"] = 1
        py = table(obj["python"])
        del py["wheel_targets"]
        py["wheel_tags"] = ["py3-none-any"]
    elif mutation == "mixed":
        table(obj["python"])["wheel_tags"] = ["py3-none-any"]
    elif mutation == "project":
        table(obj["python"])["wheel_stem"] = "wrong_project"
    elif mutation == "duplicate_image":
        obj["oci"] = [*array(obj["oci"]), array(obj["oci"])[0]]
    encoded = json.dumps(obj)
    if mutation == "duplicate":
        encoded = encoded.replace(
            '"schema_version": 2', '"schema_version": 2, "schema_version": 2'
        )
    path = tmp_path / "policy.json"
    path.write_text(encoded)
    with pytest.raises(PublicationError):
        Policy.load(path)


def test_real_normalized_metadata_and_compressed_tags(tmp_path: Path) -> None:
    import io
    import tarfile
    import zipfile

    from release_ci.archives import validate_archive
    from release_ci.common import decode, string, table
    from release_ci.policy import Policy

    root = Path(__file__).resolve().parents[1]
    fixtures = root / "tests/fixtures"
    for summary in fixtures.glob("*-representative-summary.json"):
        project = summary.name.removesuffix("-representative-summary.json")
        obj = table(decode(summary.read_bytes()))
        policy = Policy.load(root / "examples" / (project + ".json"))
        version = parse_version("v" + string(obj["version"]), "v")
        wheel = tmp_path / string(obj["wheel"])
        metadata = (fixtures / (project + "-representative-METADATA")).read_bytes()
        wheel_info = fixtures / (project + "-representative-WHEEL")
        with zipfile.ZipFile(wheel, "w") as archive:
            stem = policy.wheel_stem + "-" + version.package + ".dist-info/"
            archive.writestr(stem + "METADATA", metadata)
            # Sidecar-only fixtures cover real project spelling; full fixtures
            # additionally cover the exact independently fetched WHEEL tags.
            content = (
                wheel_info.read_bytes()
                if wheel_info.exists()
                else b"Wheel-Version: 1.0\nTag: py3-none-any\n"
            )
            if not wheel_info.exists():
                content = (
                    "Wheel-Version: 1.0\nTag: "
                    + wheel.name.removesuffix(".whl").split("-", 2)[2]
                    + "\n"
                ).encode()
            archive.writestr(stem + "WHEEL", content)
        validate_archive(wheel, policy, version)
        if "sdist" not in obj:
            continue
        sdist = tmp_path / string(obj["sdist"])
        info = (fixtures / (project + "-representative-PKG-INFO")).read_bytes()
        with tarfile.open(sdist, "w:gz") as archive:
            member = tarfile.TarInfo(sdist.name.removesuffix(".tar.gz") + "/PKG-INFO")
            member.size = len(info)
            archive.addfile(member, io.BytesIO(info))
        validate_archive(sdist, policy, version)
