"""Independent schema and package-tag boundaries."""

from pathlib import Path

import pytest

from release_ci.common import PublicationError
from release_ci.config import expand_tag, parse_version
from tests.project import fleet_project


def test_compressed_wheel_tags_expand_cartesian_product() -> None:
    assert expand_tag("py2.py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64") == {
        "py2-none-manylinux_2_17_x86_64",
        "py3-none-manylinux_2_17_x86_64",
        "py2-none-manylinux2014_x86_64",
        "py3-none-manylinux2014_x86_64",
    }


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3rc1", "1.2.3-rc.1"])
def test_version_normalization(version: str) -> None:
    parsed = parse_version("v" + version)
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

    from release_ci.config import Config

    config = Config.load(Path(__file__).resolve().parents[1] / "examples/audio-hotplug")
    config = replace(
        config,
        github_distributions=False,
        asset_templates=(f"audio_hotplug-{placeholder}.tar.gz",),
    )
    version = parse_version(tag)
    from release_ci.archives import distributions
    from release_ci.targets import WheelPlan
    from tests.test_pure import pure_archives

    pure_archives(tmp_path, config, version)
    if collides:
        with pytest.raises(PublicationError, match="overlap"):
            distributions(tmp_path, config, version, WheelPlan.pure("1" * 40))
    else:
        assert set(
            distributions(tmp_path, config, version, WheelPlan.pure("1" * 40))
        ).isdisjoint(config.asset_names(version))


@pytest.mark.parametrize(
    "version", ["1.2", "01.2.3", "1.2.3/dev", "1.2.3+local", "1.2.3\n"]
)
def test_unsupported_version_rejected(version: str) -> None:
    with pytest.raises(PublicationError):
        parse_version("v" + version)


def test_every_fleet_config_matches_independently_fetched_manifest() -> None:
    from release_ci.common import array, decode, string, table
    from release_ci.config import Config, render
    from release_ci.planner import plan
    from release_ci.targets import WheelPlan

    root = Path(__file__).resolve().parents[1]
    for fixture in (root / "tests/fixtures").glob("*.json"):
        if fixture.name.endswith("summary.json"):
            continue
        obj = table(decode(fixture.read_bytes()))
        config = Config.load(fleet_project(fixture.stem))
        version = parse_version("v" + string(obj["version"]))
        names = {string(n) for n in array(obj["filenames"])}
        expected = (
            WheelPlan.pure("1" * 40)
            if config.wheel_targets == "pure"
            else plan(fleet_project(fixture.stem), "1" * 40)[1]
        )
        expected.coverage(
            {
                n: n.removesuffix(".whl").split("-", 2)[2]
                for n in names
                if n.endswith(".whl")
            }
        )
        assert names - {n for n in names if n.endswith(".whl")} == {
            render(config.sdist, version)
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
        "empty_targets",
        "invalid_platform",
        "malformed",
        "symlink",
    ],
)
def test_strict_config_rejects_invalid_inputs(tmp_path: Path, mutation: str) -> None:
    from release_ci.config import Config

    base = '[project]\nname="LedFx"\ndynamic=["version"]\n'
    settings = {
        "unknown": '[tool.release-ci]\ncommand="publish anything"',
        "nested_unknown": '[[tool.release-ci.oci]]\nimage="ghcr.io/ledfx/ledfx"\nplatforms=["linux/amd64"]\nskip-validation=true',
        "duplicate": "[tool.release-ci]\nassets=[]\nassets=[]",
        "schema_bool": "[tool.release-ci]\nschema_version=true",
        "template": '[tool.release-ci]\nassets=["../{version}.zip"]',
        "registry": '[tool.release-ci]\npypi_url="https://test.pypi.org"',
        "wheel_targets": '[tool.release-ci]\nwheel_targets="auto"',
        "legacy": '{"schema_version": 2}',
        "mixed": '[tool.release-ci]\nwheel_tags=["py3-none-any"]',
        "project": '[tool.release-ci]\nproject="different"',
        "duplicate_image": (
            '[[tool.release-ci.oci]]\nimage="ghcr.io/ledfx/ledfx"\nplatforms=["linux/amd64"]\n'
            * 2
        ),
        "empty_targets": "[tool.release-ci]\ntargets=[]",
        "invalid_platform": '[[tool.release-ci.targets]]\nplatform="linux"\narch="auto"\nrunner="ubuntu-latest"',
        "malformed": "[project",
        "symlink": "",
    }
    path = tmp_path / "pyproject.toml"
    path.write_text(base + settings[mutation])
    if mutation == "symlink":
        saved = tmp_path / "saved.toml"
        path.rename(saved)
        path.symlink_to(saved)
    with pytest.raises(PublicationError):
        Config.load(tmp_path)


@pytest.mark.parametrize(
    "name,stem",
    [
        ("LedFx", "ledfx"),
        ("samplerate-ledfx", "samplerate_ledfx"),
        ("Some.Project_name", "some_project_name"),
    ],
)
def test_metadata_names_pure_defaults_and_dynamic_version(
    tmp_path: Path, name: str, stem: str
) -> None:
    from release_ci.config import Config

    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname="{name}"\ndynamic=["version"]\n'
    )
    config = Config.load(tmp_path)
    assert config.project == name and config.wheel_stem == stem
    assert config.sdist == stem + "-{version}.tar.gz"
    assert config.wheel_targets == "pure" and config.github_distributions
    assert (
        config.asset_templates == ()
        and config.oci == ()
        and config.static_version is None
    )


@pytest.mark.parametrize(
    "metadata",
    [
        'version="1.2.3"\ndynamic=["version"]',
        "version=3",
        "dynamic=[]",
        'version="1.2.3+local"',
    ],
)
def test_invalid_version_metadata(tmp_path: Path, metadata: str) -> None:
    from release_ci.config import Config

    (tmp_path / "pyproject.toml").write_text('[project]\nname="example"\n' + metadata)
    with pytest.raises(PublicationError):
        Config.load(tmp_path)


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2.3", "v1.2.3-rc.1"])
def test_context_derives_exact_provenance_workflow(tag: str) -> None:
    from release_ci.config import caller_workflow

    assert (
        caller_workflow(
            "LedFx/python-samplerate-ledfx",
            "LedFx/python-samplerate-ledfx/.github/workflows/build.yml@refs/tags/"
            + tag,
            tag,
        )
        == ".github/workflows/build.yml"
    )


@pytest.mark.parametrize(
    "ref",
    [
        "other/repo/.github/workflows/ci.yml@refs/tags/v1.2.3",
        "LedFx/repo/.github/workflows/ci.yml@refs/heads/main",
        "LedFx/repo/.github/workflows/../ci.yml@refs/tags/v1.2.3",
        "LedFx/repo/.github/workflows/ci.yml@refs/tags/v1.2.4",
        "https://example.org/ci.yml",
    ],
)
def test_invalid_caller_context(ref: str) -> None:
    from release_ci.config import caller_workflow

    with pytest.raises(PublicationError):
        caller_workflow("LedFx/repo", ref, "v1.2.3")


def test_real_normalized_metadata_and_compressed_tags(tmp_path: Path) -> None:
    import io
    import tarfile
    import zipfile

    from release_ci.archives import validate_archive
    from release_ci.common import decode, string, table
    from release_ci.config import Config

    root = Path(__file__).resolve().parents[1]
    fixtures = root / "tests/fixtures"
    for summary in fixtures.glob("*-representative-summary.json"):
        project = summary.name.removesuffix("-representative-summary.json")
        obj = table(decode(summary.read_bytes()))
        config = Config.load(fleet_project(project))
        version = parse_version("v" + string(obj["version"]))
        wheel = tmp_path / string(obj["wheel"])
        metadata = (fixtures / (project + "-representative-METADATA")).read_bytes()
        wheel_info = fixtures / (project + "-representative-WHEEL")
        with zipfile.ZipFile(wheel, "w") as archive:
            stem = config.wheel_stem + "-" + version.package + ".dist-info/"
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
        validate_archive(wheel, config, version)
        if "sdist" not in obj:
            continue
        sdist = tmp_path / string(obj["sdist"])
        info = (fixtures / (project + "-representative-PKG-INFO")).read_bytes()
        with tarfile.open(sdist, "w:gz") as archive:
            member = tarfile.TarInfo(sdist.name.removesuffix(".tar.gz") + "/PKG-INFO")
            member.size = len(info)
            archive.addfile(member, io.BytesIO(info))
        validate_archive(sdist, config, version)


def test_optional_release_preferences_have_strict_types_and_defaults(
    tmp_path: Path,
) -> None:
    from release_ci.config import Config

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="example"\nversion="1.2.3"\n[tool.release-ci]\ngithub-distributions=false\nassets=["app-{tag_version}.zip"]\n[[tool.release-ci.oci]]\nimage="ghcr.io/ledfx/example"\nplatforms=["linux/amd64"]\n'
    )
    config = Config.load(tmp_path)
    assert not config.github_distributions
    assert config.asset_names(parse_version("v1.2.3")) == {"app-1.2.3.zip"}
    assert not config.oci[0].promote_latest
    assert config.oci[0].version_tag == "{tag_version}"
    path = tmp_path / "pyproject.toml"
    path.write_text(
        path.read_text().replace(
            "github-distributions=false", 'github-distributions="false"'
        )
    )
    with pytest.raises(PublicationError):
        Config.load(tmp_path)


def test_pyproject_mutation_during_load_is_rejected_before_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from release_ci.config import Config
    from release_ci.publisher import Publisher
    from tests.project import write_project
    from tests.test_transaction import REPO, SHA, VERSION, Remote

    project = write_project(tmp_path / "project", pure=True)
    original_load = Config.load

    def changed_load(path: Path) -> Config:
        config = original_load(path)
        (path / "pyproject.toml").write_text(
            (path / "pyproject.toml").read_text()
            + "\n# changed during configuration loading\n"
        )
        return config

    monkeypatch.setattr(Config, "load", changed_load)
    remote = Remote()
    with pytest.raises(PublicationError, match="changed while loading"):
        Publisher(
            tmp_path / "dist",
            tmp_path / "snapshot",
            REPO,
            "v" + VERSION,
            SHA,
            project=project,
            workflow_ref=REPO + "/.github/workflows/ci.yml@refs/tags/v" + VERSION,
            command=remote,
            fetch=remote.fetch,
        )
    assert not remote.commands
