"""Caller identity, paths and Action outputs are enforced before any write."""

import json
from pathlib import Path

import pytest

from release_ci.__main__ import main
from release_ci.publisher import Publisher
from tests.project import write_project
from tests.test_transaction import (
    PROJECT,
    REPO,
    SHA,
    VERSION,
    Remote,
    distributions,
    native_plan,
)


@pytest.mark.parametrize("pure", [False, True])
@pytest.mark.parametrize(
    "invalid", [None, "workflow", "repository", "path", "snapshot", "plan", "legacy"]
)
def test_cli_preflight_identity_and_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str | None,
    pure: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    temporary = tmp_path / "temp"
    temporary.mkdir()
    dist = workspace / "dist"
    distributions(dist, pure=pure)
    config = write_project(workspace / "project", pure=pure)
    if invalid == "legacy":
        (config / "pyproject.toml").write_text('{"schema_version": 2}')
    snapshot = temporary / "snapshot.json"
    output = tmp_path / "outputs"
    for key, value in {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/tags/v" + VERSION,
        "GITHUB_REF_NAME": "v" + VERSION,
        "GITHUB_SHA": SHA,
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_WORKFLOW_REF": REPO + "/.github/workflows/ci.yml@refs/tags/v" + VERSION,
        "GITHUB_WORKSPACE": str(workspace),
        "RUNNER_TEMP": str(temporary),
        "GITHUB_OUTPUT": str(output),
        "GH_TOKEN": "offline-fixture-token",
    }.items():
        monkeypatch.setenv(key, value)
    if invalid == "workflow":
        monkeypatch.setenv(
            "GITHUB_WORKFLOW_REF",
            REPO + "/.github/workflows/ci.yml@refs/heads/main",
        )
    elif invalid == "repository":
        monkeypatch.setenv("GITHUB_REPOSITORY", "other/repository")
    elif invalid == "path":
        config = PROJECT
    elif invalid == "snapshot":
        snapshot = workspace / "bad-snapshot.json"
    remote = Remote()

    def publisher(
        dist: Path,
        snapshot: Path,
        repository: str,
        tag: str,
        sha: str,
        *,
        project: Path,
        workflow_ref: str,
        assets: Path | None,
        docker_digests: Path | None,
        wheel_plan: str | None,
    ) -> Publisher:
        return Publisher(
            dist,
            snapshot,
            repository,
            tag,
            sha,
            project=project,
            workflow_ref=workflow_ref,
            assets=assets,
            docker_digests=docker_digests,
            wheel_plan=wheel_plan,
            command=remote,
            fetch=remote.fetch,
        )

    monkeypatch.setattr("release_ci.__main__.Publisher", publisher)
    if invalid is None and pure:
        monkeypatch.chdir(config)
    monkeypatch.setattr(
        "sys.argv",
        [
            "release-ci",
            "prepare",
            *([] if invalid is None and pure else ["--project", str(config)]),
            "--dist",
            str(dist),
            "--snapshot",
            str(snapshot),
            *(
                ["--wheel-plan", native_plan() if pure else "{}"]
                if invalid == "plan"
                else (
                    []
                    if pure
                    else [
                        "--wheel-plan",
                        native_plan() if invalid == "legacy" else native_plan(config),
                    ]
                )
            ),
        ],
    )
    assert main() == (0 if invalid is None else 1)
    assert not remote.writes
    if invalid is None:
        assert dict(line.split("=", 1) for line in output.read_text().splitlines()) == {
            "already_published": "false",
            "pypi_upload": "true",
        }
        assert json.loads(snapshot.read_text())["repository"] == REPO
    else:
        assert not remote.commands
        assert not snapshot.exists()
        if invalid in ("plan", "legacy"):
            assert (
                "wheel plan" if invalid == "plan" else "pyproject.toml"
            ) in capsys.readouterr().err


def test_composite_preserves_caller_authority_and_quotes_inputs() -> None:
    root = Path(__file__).resolve().parents[1]
    action = (root / "actions/release/action.yml").read_text()
    assert "using: composite" in action
    assert "workflow_call" not in action and "id-token:" not in action
    run = action.split("      run: |", 1)[1]
    assert "${{ inputs." not in run
    assert '"${args[@]}"' in run
    assert 'python3 "$ACTION_ROOT/entrypoint.py"' in run


def test_removed_json_cli_input_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "release-ci",
            "prepare",
            "--policy",
            "release-policy.json",
            "--dist",
            "dist",
            "--snapshot",
            "snapshot",
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
