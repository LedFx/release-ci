"""Consumer authority remains explicit around the pinned shared transaction."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_workflow_preserves_identity_gates_and_same_run_artifacts() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    job = workflow.split("\n  publish-release:\n", 1)[1]
    for required in (
        "needs: [plan, ci-passed]",
        "github.repository == 'LedFx/ledfx-senders'",
        "github.event_name == 'push'",
        "startsWith(github.ref, 'refs/tags/v')",
        "needs.plan.outputs.release == 'true'",
        "environment: pypi",
        "queue: max",
        "cancel-in-progress: false",
        "name: sender-dist",
        "permission-contents: write",
        "permission-attestations: write",
    ):
        assert required in job
    assert "run-id:" not in job
    assert workflow.count("id-token: write") == 1
    pins = re.findall(
        r"uses: LedFx/release-ci/actions/release@([0-9a-f]{40}) # (v[0-9]+\.[0-9]+\.[0-9]+)\s*$",
        job,
        re.MULTILINE,
    )
    assert len(pins) == 3 and len(set(pins)) == 1
    planning = re.findall(
        r"uses: LedFx/release-ci/actions/plan@([0-9a-f]{40}) # (v[0-9]+\.[0-9]+\.[0-9]+)\s*$",
        workflow,
        re.MULTILINE,
    )
    assert planning == [pins[0]]
    assert job.count("wheel-plan: ${{ needs.plan.outputs.wheel-plan }}") == 3
    assert "matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}" in workflow
    assert job.count("uses: LedFx/release-ci/actions/release@") == 3
    assert job.count("policy: release-tools/.github/release-policy.json") == 3
    assert (
        job.index("phase: prepare")
        < job.index("uses: actions/attest@")
        < job.index("phase: check-upload")
        < job.index("uses: pypa/gh-action-pypi-publish@")
        < job.index("phase: finalize")
    )
    assert "bundle-path" in job and "sender-release-snapshot.json" in job
    assert "softprops" not in workflow and "--clobber" not in workflow


def test_explicit_policy_keeps_native_portable_matrix() -> None:
    policy = json.loads((ROOT / ".github/release-policy.json").read_text())
    assert policy["repository"] == "LedFx/ledfx-senders"
    assert policy["workflow"] == ".github/workflows/ci.yml"
    assert policy["python"]["project"] == "ledfx-senders"
    assert policy["schema_version"] == 2
    assert policy["python"]["wheel_targets"] == "cibuildwheel"
    assert "wheel_tags" not in policy["python"]
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    rows = config["tool"]["release-ci"]["targets"]
    assert rows and len({(row["platform"], row["arch"]) for row in rows}) == len(rows)
    dependencies = config["dependency-groups"]["wheel-build"]
    assert len(dependencies) == 1
    assert re.fullmatch(
        r"cibuildwheel(?:\[uv\])?==[0-9]+\.[0-9]+\.[0-9]+", dependencies[0]
    )
    assert policy["python"]["sdist"] == "ledfx_senders-{version}.tar.gz"
    assert policy["github_assets"] == {"distributions": True, "files": []}
    assert policy["oci"] == []
