"""Caller identity, paths and Action outputs are enforced before any write."""

import json
from pathlib import Path

import pytest

from release_ci.__main__ import main
from release_ci.publisher import Publisher
from tests.test_transaction import POLICY, REPO, SHA, VERSION, Remote, distributions


@pytest.mark.parametrize(
    "invalid", [None, "workflow", "repository", "path", "snapshot"]
)
def test_cli_preflight_identity_and_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str | None
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    temporary = tmp_path / "temp"
    temporary.mkdir()
    dist = workspace / "dist"
    distributions(dist)
    policy = workspace / "policy.json"
    policy.write_bytes(POLICY.read_bytes())
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
            REPO + "/.github/workflows/other.yml@refs/tags/v" + VERSION,
        )
    elif invalid == "repository":
        monkeypatch.setenv("GITHUB_REPOSITORY", "other/repository")
    elif invalid == "path":
        policy = POLICY
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
        policy: Path,
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
            policy=policy,
            assets=assets,
            docker_digests=docker_digests,
            wheel_plan=wheel_plan,
            command=remote,
            fetch=remote.fetch,
        )

    monkeypatch.setattr("release_ci.__main__.Publisher", publisher)
    monkeypatch.setattr(
        "sys.argv",
        [
            "release-ci",
            "prepare",
            "--policy",
            str(policy),
            "--dist",
            str(dist),
            "--snapshot",
            str(snapshot),
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


def test_composite_preserves_caller_authority_and_quotes_inputs() -> None:
    root = Path(__file__).resolve().parents[1]
    action = (root / "actions/release/action.yml").read_text()
    assert "using: composite" in action
    assert "workflow_call" not in action and "id-token:" not in action
    run = action.split("      run: |", 1)[1]
    assert "${{ inputs." not in run
    assert '"${args[@]}"' in run
    assert 'python3 "$ACTION_ROOT/entrypoint.py"' in run
