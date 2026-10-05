"""Offline registry transactions, including partial publication and latest veto."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from release_ci.common import MissingArtifact, PublicationError
from release_ci.config import Config, Image, parse_version
from release_ci.oci import OCI, Children
from release_ci.publisher import Publisher
from tests.project import ROOT, write_project
from tests.test_transaction import (
    REPO,
    SHA,
    VERSION,
    Remote,
    distributions,
    native_plan,
)


class Registry:
    def __init__(self) -> None:
        self.indexes: dict[str, dict[str, str]] = {}
        self.digests: dict[str, str] = {}
        self.writes: list[list[str]] = []
        self.attestations: list[list[str]] = []
        self.fail_attestation = False

    def put(self, ref: str, children: dict[str, str]) -> str:
        digest = (
            "sha256:"
            + hashlib.sha256(json.dumps(children, sort_keys=True).encode()).hexdigest()
        )
        image = ref.split("@", 1)[0] if "@" in ref else ref.rsplit(":", 1)[0]
        self.indexes[ref] = children.copy()
        self.indexes[f"{image}@{digest}"] = children.copy()
        self.digests[ref] = digest
        self.digests[f"{image}@{digest}"] = digest
        return digest

    def __call__(self, arguments: list[str]) -> bytes:
        if arguments[:3] == ["gh", "attestation", "verify"]:
            if self.fail_attestation:
                raise PublicationError("OCI attestation rejected")
            assert arguments[3].startswith("oci://")
            assert "--deny-self-hosted-runners" in arguments
            self.attestations.append(arguments)
            return b"verified"
        assert arguments[:3] == ["docker", "buildx", "imagetools"]
        if arguments[3] == "inspect":
            ref = arguments[4]
            if ref not in self.indexes:
                raise MissingArtifact("OCI absent fixture")
            if "--format" in arguments:
                return json.dumps({"digest": self.digests[ref]}).encode()
            return json.dumps(
                {
                    "manifests": [
                        {
                            "platform": {"os": "linux", "architecture": arch},
                            "digest": digest,
                        }
                        for arch, digest in self.indexes[ref].items()
                    ]
                }
            ).encode()
        assert arguments[3:5] == ["create", "--tag"]
        target = arguments[5]
        children: dict[str, str] = {}
        for ref in arguments[6:]:
            children.update(self.indexes[ref])
        self.put(target, children)
        self.writes.append(arguments)
        return b"created"


@pytest.fixture
def registry(tmp_path: Path) -> tuple[OCI, Registry, Children]:
    images = tuple(
        Image(image, ("linux/amd64", "linux/arm64"), "{tag_version}", True)
        for image in ("ghcr.io/ledfx/ledfx", "docker.io/ledfxorg/ledfx")
    )
    directory = tmp_path / "receipts"
    directory.mkdir()
    remote = Registry()
    children: Children = {}
    for arch, digit in (("amd64", "a"), ("arm64", "b")):
        digest = "sha256:" + digit * 64
        refs: list[str] = []
        for image in images:
            ref = f"{image.image}@{digest}"
            refs.append(ref)
            remote.put(ref, {arch: digest})
            children.setdefault(image.image, {})[arch] = digest
        (directory / f"docker-{arch}.txt").write_text("\n".join(refs))
    return (
        OCI(images, directory, parse_version("v" + VERSION), SHA, remote),
        remote,
        children,
    )


def draft() -> Mapping[str, object]:
    return {"draft": True}


def test_promote_exact_children_attest_and_retry(
    registry: tuple[OCI, Registry, Children],
) -> None:
    oci, remote, children = registry
    assert oci.source_children() == children
    digests = oci.promote(children, draft)
    assert len(remote.writes) == 4  # Two registries × version and source SHA.
    assert oci.promote(children, draft) == digests
    assert len(remote.writes) == 4
    assert (
        oci.verify_attestations(
            children, REPO, ".github/workflows/ci.yml", "v" + VERSION
        )
        == digests
    )
    assert len(remote.attestations) == 2
    oci.promote_latest(children, oci.digests(children), draft, lambda: True)
    assert len(remote.writes) == 6
    assert all(
        remote.digests[image + ":latest"] == digest for image, digest in digests.items()
    )


def test_public_mode_never_repairs_missing_indexes(
    registry: tuple[OCI, Registry, Children],
) -> None:
    oci, remote, children = registry
    with pytest.raises(MissingArtifact):
        oci.promote(children, lambda: {"draft": False})
    assert not remote.writes


def test_latest_veto_does_not_block_versioned_release(
    registry: tuple[OCI, Registry, Children],
) -> None:
    oci, remote, children = registry
    oci.promote(children, draft)
    oci.promote_latest(children, oci.digests(children), draft, lambda: False)
    assert len(remote.writes) == 4
    assert not any(c[5].endswith(":latest") for c in remote.writes)


def test_changed_latest_eligibility_stops_second_registry(
    registry: tuple[OCI, Registry, Children],
) -> None:
    oci, remote, children = registry
    oci.promote(children, draft)
    calls = 0

    def eligible() -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    oci.promote_latest(children, oci.digests(children), draft, eligible)
    assert len(remote.writes) == 5


@pytest.mark.parametrize("phase", ["before", "after_guard"])
def test_conflicting_version_tag_is_never_replaced(
    registry: tuple[OCI, Registry, Children], phase: str
) -> None:
    oci, remote, children = registry
    ref = oci.images[0].image + ":" + VERSION

    def conflict() -> Mapping[str, object]:
        remote.put(ref, {"amd64": "sha256:" + "f" * 64})
        return draft()

    if phase == "before":
        conflict()
    with pytest.raises(PublicationError, match="conflict"):
        oci.promote(children, conflict)
    assert not remote.writes


@pytest.mark.parametrize("change", ["duplicate", "missing", "unknown"])
def test_receipt_contract_rejects_invalid_sources(
    registry: tuple[OCI, Registry, Children], change: str
) -> None:
    oci, remote, _ = registry
    assert oci.directory is not None
    path = oci.directory / "docker-amd64.txt"
    if change == "duplicate":
        path.write_text(path.read_text() + "\n" + path.read_text())
    elif change == "missing":
        path.unlink()
    else:
        path.write_text("ghcr.io/another/project@sha256:" + "a" * 64)
    with pytest.raises(PublicationError):
        oci.source_children()
    assert not remote.writes


@pytest.mark.parametrize("pure", [False, True])
def test_full_package_assets_and_oci_transaction(
    tmp_path: Path, registry: tuple[OCI, Registry, Children], pure: bool
) -> None:
    oci, registry_remote, _ = registry
    ledfx = Config.load(ROOT / "examples/ledfx")
    path = write_project(
        tmp_path / "project",
        pure=pure,
        settings={
            "github-distributions": False,
            "assets": list(ledfx.asset_templates),
            "oci": [
                {
                    "image": image.image,
                    "platforms": list(image.platforms),
                    "promote-latest": image.promote_latest,
                }
                for image in ledfx.oci
            ],
        },
    )
    dist = tmp_path / "dist"
    distributions(dist, pure=pure)
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ledfx.asset_templates:
        assert isinstance(name, str)
        (assets / name.replace("{tag_version}", VERSION)).write_bytes(b"frozen fixture")
    github = Remote()

    def command(arguments: list[str]) -> bytes:
        if arguments[0] == "docker" or (
            arguments[:3] == ["gh", "attestation", "verify"]
            and arguments[3].startswith("oci://")
        ):
            return registry_remote(arguments)
        return github(arguments)

    publisher = Publisher(
        dist,
        tmp_path / "snapshot.json",
        REPO,
        "v" + VERSION,
        SHA,
        project=path,
        workflow_ref=REPO + "/.github/workflows/ci.yml@refs/tags/v" + VERSION,
        wheel_plan=None if pure else native_plan(path),
        assets=assets,
        docker_digests=oci.directory,
        command=command,
        fetch=github.fetch,
    )
    publisher.prepare()
    assert not github.writes and not registry_remote.writes
    github.pypi = publisher.local_inputs()
    publisher.promote()
    assert len(registry_remote.writes) == 4
    registry_remote.fail_attestation = True
    with pytest.raises(PublicationError, match="attestation"):
        publisher.finalize()
    assert not github.writes
    registry_remote.fail_attestation = False
    publisher.finalize()
    assert len(github.writes) == 5  # Four frozen archives only, then draft PATCH.
    assert len(registry_remote.writes) == 6
    assert github.release["draft"] is False
    publisher.prepare()
    publisher.finalize()
    assert len(github.writes) == 5 and len(registry_remote.writes) == 6


def test_latest_never_uses_unattested_replacement_descriptor(
    registry: tuple[OCI, Registry, Children],
) -> None:
    oci, remote, children = registry
    attested = oci.promote(children, draft)
    image = oci.images[0].image
    changed = "sha256:" + "e" * 64
    remote.indexes[image + "@" + changed] = children[image].copy()
    remote.digests[image + ":" + VERSION] = changed
    with pytest.raises(PublicationError, match="after attestation"):
        oci.promote_latest(children, attested, draft, lambda: True)
    assert len(remote.writes) == 4
