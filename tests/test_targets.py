"""Real build configuration selection and strict repaired wheel coverage."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from release_ci.common import PublicationError, array, decode, string, table
from release_ci.planner import plan
from release_ci.publisher import Publisher
from release_ci.targets import WheelPlan
from tests.test_transaction import POLICY, REPO, SHA, VERSION, Remote, distributions

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/planning"


def selected(*identifiers: str) -> WheelPlan:
    return WheelPlan.load(
        {
            "schema_version": 1,
            "source_sha": SHA,
            "cibuildwheel": "4.2.1",
            "targets": list(identifiers),
        },
        SHA,
    )


@pytest.mark.parametrize(
    "consumer,fixture,count,wheels",
    [
        ("ledfx-senders", "ledfx-senders", 35, 35),
        ("aubio-ledfx", "aubio-ledfx", 25, 25),
        ("pyfastnoiselite-ledfx", "pyfastnoiselite-ledfx", 45, 9),
        ("python-samplerate-ledfx", "samplerate-ledfx", 25, 25),
    ],
)
def test_real_cli_selections_cover_retained_release_filenames(
    consumer: str, fixture: str, count: int, wheels: int
) -> None:
    matrix, expected = plan(FIXTURES / consumer, SHA)
    retained = table(
        decode((ROOT / "tests/fixtures" / (fixture + ".json")).read_bytes())
    )
    names = [string(item) for item in array(retained["filenames"])]
    tags = {
        name: name.removesuffix(".whl").split("-", 2)[2]
        for name in names
        if name.endswith(".whl")
    }
    expected.coverage(tags)
    assert len(expected.targets) == count and len(tags) == wheels
    assert len(array(matrix["include"])) == (
        6 if consumer == "pyfastnoiselite-ledfx" else 5
    )
    # Losing a complete runner upload still leaves its targets in the central plan.
    with pytest.raises(PublicationError, match="Missing wheel targets"):
        expected.coverage(
            {name: tag for name, tag in tags.items() if "macosx" not in tag}
        )


def test_python_and_platform_edits_automatically_change_real_plan(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "ledfx-senders", project)
    config = project / "pyproject.toml"
    config.write_text(
        config.read_text().replace("cp3{11,12,13,14,15}-*", "cp3{11,12,13,14}-*")
    )
    _, before = plan(project, SHA)
    config.write_text(
        config.read_text().replace("cp3{11,12,13,14}-*", "cp3{11,12,13,14,15}-*")
    )
    _, after = plan(project, SHA)
    assert {t.identifier for t in after.targets} - {
        t.identifier for t in before.targets
    } == {
        "cp315-" + suffix
        for suffix in (
            "manylinux_x86_64",
            "manylinux_aarch64",
            "win_amd64",
            "macosx_x86_64",
            "macosx_arm64",
        )
    }
    # Add a supported platform once; no wheel policy or version list changes.
    config.write_text(
        config.read_text().replace(" *-win_arm64", "")
        + """
[[tool.release-ci.targets]]
platform = "windows"
arch = "ARM64"
runner = "windows-11-arm"
"""
    )
    matrix, extended = plan(project, SHA)
    assert len(array(matrix["include"])) == 6
    assert {t.identifier for t in extended.targets} - {
        t.identifier for t in after.targets
    } == {
        "cp" + version + "-win_arm64"
        for version in ("311", "312", "313", "314", "314t", "315", "315t")
    }


def test_compressed_tags_and_abi3_reuse_are_one_output() -> None:
    expected = selected("cp311-manylinux_x86_64", "cp315-manylinux_x86_64")
    expected.coverage(
        {"abi3.whl": "cp311-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64"}
    )


def test_free_threaded_abi_does_not_cover_gil_target() -> None:
    expected = selected("cp314-win_amd64", "cp314t-win_amd64")
    with pytest.raises(
        PublicationError, match="Missing wheel targets: cp314-win_amd64"
    ):
        expected.coverage({"threaded.whl": "cp314-cp314t-win_amd64"})
    expected.coverage(
        {"gil.whl": "cp314-cp314-win_amd64", "threaded.whl": "cp314-cp314t-win_amd64"}
    )


@pytest.mark.parametrize(
    "identifiers,tags,error",
    [
        (("cp314t-win_amd64",), {"abi3": "cp314-abi3-win_amd64"}, "baseline"),
        (("cp312-win_amd64",), {"abi3": "cp311-abi3-win_amd64"}, "baseline"),
        (
            ("cp311-win_amd64", "cp312-win_amd64"),
            {"abi3": "cp311-abi3-win_amd64", "cp312": "cp312-cp312-win_amd64"},
            "Overlapping",
        ),
        (("cp311-win_amd64",), {"extra": "cp312-cp312-win_amd64"}, "Unexpected"),
        (("cp314-win_amd64",), {"wrongabi": "cp314-cp313-win_amd64"}, "Invalid wheel"),
        (("cp314-win_amd64",), {"wrongpy": "cp314t-cp314t-win_amd64"}, "Invalid wheel"),
        (("cp314-win_amd64",), {"compressed": "cp314.cp315-cp314-win_amd64"}, "Mixed"),
        (("cp314-win_amd64",), {"compressed": "cp314-cp314.abi3-win_amd64"}, "Mixed"),
        (
            ("cp311-manylinux_x86_64",),
            {"mixed": "cp311-cp311-manylinux_2_17_x86_64.musllinux_1_2_x86_64"},
            "Mixed",
        ),
        (
            ("cp311-manylinux_x86_64",),
            {"mixed": "cp311-cp311-manylinux_2_17_x86_64.manylinux_2_17_aarch64"},
            "Mixed",
        ),
        (
            ("cp311-macosx_arm64",),
            {"universal": "cp311-cp311-macosx_11_0_universal2"},
            "Unsupported",
        ),
    ],
)
def test_invalid_unexpected_and_overlapping_wheels(
    identifiers: tuple[str, ...], tags: dict[str, str], error: str
) -> None:
    with pytest.raises(PublicationError, match=error):
        selected(*identifiers).coverage(tags)


@pytest.mark.parametrize(
    "change", ["empty", "duplicate", "source", "unknown", "malformed"]
)
def test_invalid_plans_fail(change: str) -> None:
    value = selected("cp311-win_amd64").value()
    if change == "empty":
        empty: list[str] = []
        value["targets"] = empty
    elif change == "duplicate":
        value["targets"] = ["cp311-win_amd64"] * 2
    elif change == "source":
        value["source_sha"] = "2" * 40
    elif change == "unknown":
        value["extra"] = True
    else:
        value["targets"] = ["pp311-win_amd64"]
    with pytest.raises(PublicationError):
        WheelPlan.load(value, SHA)


@pytest.mark.parametrize(
    "change", ["empty", "duplicate", "arch", "metadata", "environment"]
)
def test_bad_build_rows_and_selector_environment_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    source = FIXTURES / "ledfx-senders" / "pyproject.toml"
    text = source.read_text()
    if change == "empty":
        text = text.replace("cp3{11,12,13,14,15}-* cp3{14,15}t-*", "cp399-*")
    elif change == "duplicate":
        text += '\n[[tool.release-ci.targets]]\nplatform="linux"\narch="x86_64"\nrunner="ubuntu-latest"\n'
    elif change == "arch":
        text = text.replace('arch = "x86_64"', 'arch = "auto"')
    elif change == "metadata":
        text = text.replace('target = "linux-x86_64"', "target = 1")
    else:
        monkeypatch.setenv("CIBW_BUILD", "cp311-*")
    (tmp_path / "pyproject.toml").write_text(text)
    with pytest.raises(PublicationError):
        plan(tmp_path, SHA)


def planned_transaction(tmp_path: Path) -> tuple[Publisher, Remote, str, Path]:
    dist = tmp_path / "dist"
    distributions(dist)
    value = table(decode(POLICY.read_bytes()))
    value["schema_version"] = 2
    py = table(value["python"])
    del py["wheel_tags"]
    py["wheel_targets"] = "cibuildwheel"
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(value))
    _, expected = plan(FIXTURES / "ledfx-senders", SHA)
    serialized = json.dumps(expected.value())
    remote = Remote()
    return (
        Publisher(
            dist,
            tmp_path / "snapshot.json",
            REPO,
            "v" + VERSION,
            SHA,
            policy=policy,
            wheel_plan=serialized,
            command=remote,
            fetch=remote.fetch,
        ),
        remote,
        serialized,
        policy,
    )


def test_planned_transaction_freezes_plan_and_retries(tmp_path: Path) -> None:
    publisher, remote, serialized, _ = planned_transaction(tmp_path)
    publisher.prepare()
    assert table(decode(publisher.snapshot.read_bytes()))["wheel_plan"] == decode(
        serialized
    )
    remote.pypi = publisher.local_inputs()
    publisher.finalize()
    remote.writes.clear()
    assert publisher.prepare() == {"already_published": True, "pypi_upload": False}
    publisher.finalize()
    assert not remote.writes


@pytest.mark.parametrize(
    "change", ["plan", "source", "platform", "collision", "symlink", "metadata"]
)
def test_planned_transaction_rejects_changes_before_remote_writes(
    tmp_path: Path, change: str
) -> None:
    publisher, remote, serialized, policy = planned_transaction(tmp_path)
    if change in ("plan", "source"):
        publisher.prepare()
        remote.pypi = publisher.local_inputs()
        value = table(decode(serialized))
        value["cibuildwheel" if change == "plan" else "source_sha"] = (
            "4.3.0" if change == "plan" else "2" * 40
        )
        with pytest.raises(PublicationError):
            retry = Publisher(
                publisher.dist,
                publisher.snapshot,
                REPO,
                "v" + VERSION,
                SHA,
                policy=policy,
                wheel_plan=json.dumps(value),
                command=remote,
                fetch=remote.fetch,
            )
            retry.finalize()
    else:
        wheel = next(publisher.dist.glob("*.whl"))
        if change == "platform":
            for path in publisher.dist.glob("*macosx*"):
                path.unlink()
        elif change == "collision":
            value = table(decode(policy.read_bytes()))
            value["github_assets"] = {
                "distributions": False,
                "files": [wheel.name.replace(VERSION, "{version}")],
            }
            policy.write_text(json.dumps(value))
            publisher = Publisher(
                publisher.dist,
                publisher.snapshot,
                REPO,
                "v" + VERSION,
                SHA,
                policy=policy,
                wheel_plan=serialized,
                command=remote,
                fetch=remote.fetch,
            )
        elif change == "symlink":
            saved = tmp_path / "saved.whl"
            wheel.rename(saved)
            wheel.symlink_to(saved)
        else:
            wheel.write_bytes(b"invalid metadata")
        with pytest.raises(PublicationError):
            publisher.prepare()
        assert not remote.commands
    assert not remote.writes


def test_planner_cli_outputs_are_compact_json(tmp_path: Path) -> None:
    import os
    import sys

    output = tmp_path / "outputs"
    project = tmp_path / "project"
    shutil.copytree(FIXTURES / "ledfx-senders", project)
    for arguments in (
        ["git", "init", "--quiet"],
        ["git", "add", "pyproject.toml"],
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Test configuration",
        ],
    ):
        subprocess.run(
            arguments, cwd=project, check=True, capture_output=True, timeout=30
        )
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True
    ).strip()
    result = subprocess.run(
        [sys.executable, str(ROOT / "plan_entrypoint.py"), "--source-sha", sha],
        cwd=project,
        env=dict(os.environ, GITHUB_OUTPUT=str(output)),
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert set(values) == {"matrix", "wheel-plan"}
    assert WheelPlan.load(decode(values["wheel-plan"]), sha).targets
    mismatched = subprocess.run(
        [sys.executable, str(ROOT / "plan_entrypoint.py"), "--source-sha", SHA],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert mismatched.returncode == 1 and "HEAD differs" in mismatched.stderr


def test_failed_enumeration_reports_upstream_configuration_error(
    tmp_path: Path,
) -> None:
    text = (FIXTURES / "ledfx-senders/pyproject.toml").read_text()
    text = text.replace(
        "[tool.cibuildwheel]\n", "[tool.cibuildwheel]\nnot-a-real-option = true\n"
    )
    (tmp_path / "pyproject.toml").write_text(text)
    with pytest.raises(PublicationError, match="not-a-real-option"):
        plan(tmp_path, SHA)
