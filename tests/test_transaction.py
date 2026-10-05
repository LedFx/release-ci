"""Offline publication transactions: immutable inputs, retries and provenance."""

import hashlib
import io
import json
import tarfile
import zipfile
from email.message import Message
from pathlib import Path

import pytest

from release_ci.common import PublicationError
from release_ci.policy import Policy, parse_version
from release_ci.publisher import Publisher

POLICY = Path(__file__).resolve().parents[1] / "examples/ledfx-senders.json"


def expected_names(version: str) -> set[str]:
    return Policy.load(POLICY).distribution_names(parse_version("v" + version, "v"))


SHA = "1" * 40
REPO = "LedFx/ledfx-senders"
VERSION = "0.3.0"


def distributions(directory: Path, version: str = VERSION) -> None:
    directory.mkdir()
    metadata = f"Metadata-Version: 2.4\nName: ledfx-senders\nVersion: {version}\n"
    for name in expected_names(version):
        path = directory / name
        if name.endswith(".whl"):
            tag = name.removesuffix(".whl").split("-", 2)[2]
            with zipfile.ZipFile(path, "w") as wheel:
                wheel.writestr(f"ledfx_senders-{version}.dist-info/METADATA", metadata)
                wheel.writestr(
                    f"ledfx_senders-{version}.dist-info/WHEEL",
                    f"Wheel-Version: 1.0\nTag: {tag}\n",
                )
        else:
            with tarfile.open(path, "w:gz") as archive:
                member = tarfile.TarInfo(f"ledfx_senders-{version}/PKG-INFO")
                content = metadata.encode()
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))


class Remote:
    def __init__(self, version: str = VERSION) -> None:
        self.version = version
        self.release: dict[str, object] = {
            "id": 17,
            "tag_name": "v" + version,
            "body": "Reviewed release-please notes",
            "draft": True,
            "prerelease": False,
            "assets": [],
        }
        self.assets: list[dict[str, object]] = []
        self.pypi: dict[str, str] = {}
        self.downloads: dict[int, bytes] = {}
        self.sha = SHA
        self.commands: list[list[str]] = []
        self.writes: list[list[str]] = []
        self.newer = False
        self.attestation_failure = False

    def fetch(self, version: str) -> object:
        assert version == self.version
        return {
            "urls": [
                {"filename": n, "digests": {"sha256": d}} for n, d in self.pypi.items()
            ]
        }

    def __call__(self, command: list[str]) -> bytes:
        self.commands.append(command)
        if command[:3] == ["gh", "attestation", "verify"]:
            if self.attestation_failure:
                raise PublicationError("invalid attestation")
            for flag, value in (
                ("--repo", REPO),
                ("--signer-workflow", REPO + "/.github/workflows/ci.yml"),
                ("--source-digest", SHA),
                ("--source-ref", "refs/tags/" + str(self.release["tag_name"])),
            ):
                assert command[command.index(flag) + 1] == value
            assert "--deny-self-hosted-runners" in command
            return b"verified"
        assert command[:2] == ["gh", "api"]
        endpoint = command[2]
        if "--method" in command:
            self.writes.append(command)
            method = command[command.index("--method") + 1]
            if method == "POST":
                assert f"/releases/{self.release['id']}/assets?name=" in endpoint
                path = Path(command[command.index("--input") + 1])
                assert f"Content-Length: {path.stat().st_size}" in command
                self.assets.append(
                    {
                        "id": len(self.assets) + 100,
                        "name": path.name,
                        "digest": "sha256:"
                        + hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
            else:
                assert method == "PATCH" and endpoint.endswith("/releases/17")
                assert command[command.index("-F") + 1] == "draft=false"
                self.release["draft"] = False
            return b"{}"
        if "/git/ref/tags/" in endpoint:
            return json.dumps({"object": {"type": "commit", "sha": self.sha}}).encode()
        if "/releases/assets/" in endpoint:
            return self.downloads[int(endpoint.rsplit("/", 1)[1])]
        self.release["assets"] = self.assets
        if endpoint.endswith("/releases"):
            newer: list[dict[str, object]] = (
                [{"tag_name": "v9.0.0", "draft": False, "prerelease": False}]
                if self.newer
                else []
            )
            return json.dumps([newer]).encode()
        return json.dumps(self.release).encode()


@pytest.fixture
def transaction(tmp_path: Path) -> tuple[Publisher, Remote]:
    dist = tmp_path / "dist"
    distributions(dist)
    remote = Remote()
    publisher = Publisher(
        dist,
        tmp_path / "snapshot.json",
        REPO,
        "v" + VERSION,
        SHA,
        policy=POLICY,
        command=remote,
        fetch=remote.fetch,
    )
    return publisher, remote


def upload_pypi(publisher: Publisher, remote: Remote) -> None:
    remote.pypi = publisher.local_inputs()


def test_success_attests_then_uploads_and_publishes_existing_id_last(
    transaction: tuple[Publisher, Remote],
) -> None:
    publisher, remote = transaction
    assert publisher.prepare() == {"already_published": False, "pypi_upload": True}
    assert not remote.writes
    assert publisher.check_upload()["pypi_upload"] is True
    upload_pypi(publisher, remote)
    publisher.finalize()
    assert len(remote.writes) == 37
    assert remote.writes[-1][remote.writes[-1].index("--method") + 1] == "PATCH"
    assert "make_latest=true" in remote.writes[-1]
    assert remote.release["body"] == "Reviewed release-please notes"
    attestations = [
        c for c in remote.commands if c[:3] == ["gh", "attestation", "verify"]
    ]
    assert len(attestations) == 36
    assert remote.commands.index(attestations[-1]) < remote.commands.index(
        remote.writes[0]
    )
    remote.writes.clear()
    publisher.finalize()
    assert not remote.writes
    assert publisher.prepare() == {"already_published": True, "pypi_upload": False}


def test_partial_retry_skips_only_matching_remote_files(
    transaction: tuple[Publisher, Remote],
) -> None:
    publisher, remote = transaction
    hashes = publisher.local_inputs()
    name, digest = next(iter(hashes.items()))
    remote.pypi[name] = digest
    remote.assets.append({"id": 100, "name": name, "digest": "sha256:" + digest})
    publisher.prepare()
    upload_pypi(publisher, remote)
    publisher.finalize()
    assert len(remote.writes) == 36
    assert all(name not in c[2] for c in remote.writes[:-1])


@pytest.mark.parametrize("change", ["missing", "conflicting", "legacy_download"])
def test_final_release_response_assets_must_validate_before_publication(
    transaction: tuple[Publisher, Remote], change: str
) -> None:
    publisher, remote = transaction
    publisher.prepare()
    upload_pypi(publisher, remote)
    remote.assets = [
        {"id": index + 100, "name": name, "digest": "sha256:" + digest}
        for index, (name, digest) in enumerate(remote.pypi.items())
    ]
    final_release_reads = 0
    checking_latest = False

    def command(arguments: list[str]) -> bytes:
        nonlocal final_release_reads, checking_latest
        if arguments[:2] == ["gh", "api"]:
            endpoint = arguments[2]
            if endpoint == f"repos/{REPO}/releases":
                checking_latest = True
            elif checking_latest and "/releases/tags/" in endpoint:
                final_release_reads += 1
                if final_release_reads == 2:
                    # The first GET was valid. The fresh response immediately
                    # before the draft PATCH exposes an external asset change.
                    if change == "missing":
                        remote.assets.pop()
                    elif change == "conflicting":
                        remote.assets[0]["digest"] = "sha256:" + "0" * 64
                    else:
                        name = next(iter(remote.pypi))
                        remote.assets[0]["digest"] = None
                        remote.downloads[100] = (publisher.dist / name).read_bytes()
            elif final_release_reads == 2 and "/releases/assets/" in endpoint:
                # A legacy digest download must not leave local inputs unchecked.
                path = publisher.dist / next(iter(remote.pypi))
                path.write_bytes(path.read_bytes() + b"changed during download")
        return remote(arguments)

    publisher.command = command
    with pytest.raises(PublicationError):
        publisher.finalize()
    assert not remote.writes
    assert remote.release["draft"] is True
    assert final_release_reads == 2


@pytest.mark.parametrize(
    "target", ["pypi", "github", "unexpected_pypi", "unexpected_github"]
)
def test_conflicts_prevent_all_remote_writes(
    transaction: tuple[Publisher, Remote], target: str
) -> None:
    publisher, remote = transaction
    name = next(iter(publisher.local_inputs()))
    if target.startswith("unexpected"):
        name = "unexpected.whl"
    if target.endswith("pypi") or target == "pypi":
        remote.pypi[name] = "0" * 64
    else:
        remote.assets.append({"id": 100, "name": name, "digest": "sha256:" + "0" * 64})
    with pytest.raises(PublicationError):
        publisher.prepare()
    assert not remote.writes


@pytest.mark.parametrize(
    "change", ["tag", "release_id", "body", "prerelease", "local", "snapshot"]
)
def test_frozen_identity_and_inputs_rechecked_before_writes(
    transaction: tuple[Publisher, Remote], change: str
) -> None:
    publisher, remote = transaction
    publisher.prepare()
    upload_pypi(publisher, remote)
    if change == "tag":
        remote.sha = "2" * 40
    elif change in ("release_id", "body", "prerelease"):
        remote.release[
            {"release_id": "id", "body": "body", "prerelease": "prerelease"}[change]
        ] = {"release_id": 99, "body": "changed", "prerelease": True}[change]
    elif change == "local":
        next(publisher.dist.glob("*.whl")).write_bytes(b"changed")
    else:
        publisher.snapshot.write_text("{}")
    with pytest.raises(PublicationError):
        publisher.finalize()
    assert not remote.writes


def test_attestation_failure_and_incomplete_pypi_never_publish(
    transaction: tuple[Publisher, Remote], monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher, remote = transaction

    def no_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr("release_ci.publisher.time.sleep", no_sleep)
    publisher.prepare()
    with pytest.raises(PublicationError, match="incomplete"):
        publisher.finalize()
    upload_pypi(publisher, remote)
    remote.attestation_failure = True
    with pytest.raises(PublicationError, match="attestation"):
        publisher.finalize()
    assert not remote.writes


def test_older_release_never_moves_latest_backwards(
    transaction: tuple[Publisher, Remote],
) -> None:
    publisher, remote = transaction
    publisher.prepare()
    remote.newer = True
    upload_pypi(publisher, remote)
    publisher.finalize()
    assert "make_latest=false" in remote.writes[-1]


def test_complete_matrix_is_required(transaction: tuple[Publisher, Remote]) -> None:
    publisher, remote = transaction
    next(publisher.dist.glob("*.whl")).unlink()
    with pytest.raises(PublicationError, match="file set"):
        publisher.prepare()
    assert not remote.writes


def test_pypi_rechecked_after_prepare_before_upload(
    transaction: tuple[Publisher, Remote],
) -> None:
    publisher, remote = transaction
    publisher.prepare()
    name = next(iter(publisher.local_inputs()))
    remote.pypi[name] = "0" * 64
    with pytest.raises(PublicationError, match="conflict"):
        publisher.check_upload()
    assert not remote.writes


def test_local_change_during_attestation_prevents_any_write(
    transaction: tuple[Publisher, Remote],
) -> None:
    publisher, remote = transaction
    publisher.prepare()
    upload_pypi(publisher, remote)
    changed = False

    def command(args: list[str]) -> bytes:
        nonlocal changed
        result = remote(args)
        if args[:3] == ["gh", "attestation", "verify"] and not changed:
            next(publisher.dist.glob("*.whl")).write_bytes(
                b"changed after initial check"
            )
            changed = True
        return result

    publisher.command = command
    with pytest.raises(PublicationError):
        publisher.finalize()
    assert changed and not remote.writes


def test_partial_upload_failure_can_resume_without_clobber(
    transaction: tuple[Publisher, Remote],
) -> None:
    publisher, remote = transaction
    publisher.prepare()
    upload_pypi(publisher, remote)
    failed = False

    def command(args: list[str]) -> bytes:
        nonlocal failed
        if "POST" in args and len(remote.assets) == 2 and not failed:
            failed = True
            raise PublicationError("temporary upload failure")
        return remote(args)

    publisher.command = command
    with pytest.raises(PublicationError, match="temporary"):
        publisher.finalize()
    assert len(remote.assets) == 2 and remote.release["draft"] is True
    publisher.finalize()
    posts = [c for c in remote.writes if "POST" in c]
    assert len(posts) == len({c[2] for c in posts}) == 36


@pytest.mark.parametrize("kind", ["name", "version", "abi", "unexpected", "symlink"])
def test_distribution_metadata_and_regular_file_contract(
    transaction: tuple[Publisher, Remote], kind: str
) -> None:
    publisher, remote = transaction
    path = next(publisher.dist.glob("*.whl"))
    if kind == "unexpected":
        (publisher.dist / "notes.txt").write_text("unexpected")
    elif kind == "symlink":
        saved = publisher.dist.parent / "saved.whl"
        path.rename(saved)
        path.symlink_to(saved)
    else:
        with zipfile.ZipFile(path) as wheel:
            items = {name: wheel.read(name) for name in wheel.namelist()}
        key = next(
            k for k in items if k.endswith("WHEEL" if kind == "abi" else "METADATA")
        )
        items[key] = items[key].replace(
            {"name": b"ledfx-senders", "version": VERSION.encode(), "abi": b"cp3"}[
                kind
            ],
            b"wrong",
        )
        with zipfile.ZipFile(path, "w") as wheel:
            for name, content in items.items():
                wheel.writestr(name, content)
    with pytest.raises(PublicationError):
        publisher.prepare()
    assert not remote.writes


@pytest.mark.parametrize("tag", ["v0.3.0rc1", "v0.3.0-rc.1"])
def test_prerelease_does_not_become_latest(tmp_path: Path, tag: str) -> None:
    dist = tmp_path / "dist"
    version = "0.3.0rc1"
    distributions(dist, version)
    remote = Remote(version)
    remote.release.update(tag_name=tag, prerelease=True)
    publisher = Publisher(
        dist,
        tmp_path / "snapshot.json",
        REPO,
        tag,
        SHA,
        policy=POLICY,
        command=remote,
        fetch=remote.fetch,
    )
    assert publisher.latest(remote.release) is False
    assert not remote.commands
    assert publisher.prepare()["pypi_upload"] is True
    assert json.loads(publisher.snapshot.read_text())["latest"] is False
    upload_pypi(publisher, remote)
    publisher.finalize()
    assert "make_latest=false" in remote.writes[-1]
    assert remote.release["prerelease"] is True


@pytest.mark.parametrize(
    "event,repo,ref",
    [
        ("workflow_dispatch", REPO, "refs/tags/v0.3.0"),
        ("push", "fork/ledfx-senders", "refs/tags/v0.3.0"),
        ("push", REPO, "refs/heads/main"),
    ],
)
def test_cli_cannot_publish_from_manual_fork_or_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: str, repo: str, ref: str
) -> None:
    from release_ci.__main__ import main

    for name, value in {
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REPOSITORY": repo,
        "GITHUB_REF_NAME": "v0.3.0",
        "GITHUB_REF": ref,
        "GITHUB_SHA": SHA,
        "GH_TOKEN": "offline-fake-token",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "sys.argv",
        [
            "publish_release.py",
            "prepare",
            "--policy",
            str(POLICY),
            "--dist",
            str(tmp_path / "absent"),
            "--snapshot",
            str(tmp_path / "snapshot"),
        ],
    )
    assert main() == 1
    assert not (tmp_path / "snapshot").exists()


def test_legacy_asset_without_digest_is_verified_by_bytes(
    transaction: tuple[Publisher, Remote],
) -> None:
    publisher, remote = transaction
    name = next(iter(publisher.local_inputs()))
    remote.assets.append({"id": 100, "name": name, "digest": None})
    remote.downloads[100] = (publisher.dist / name).read_bytes()
    publisher.prepare()
    assert not remote.writes
    remote.downloads[100] = b"conflicting remote bytes"
    with pytest.raises(PublicationError, match="checksum"):
        publisher.check_upload()
    assert not remote.writes


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_pypi_errors_are_not_absence(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    from urllib.error import HTTPError
    from urllib.request import Request

    from release_ci.publisher import fetch_pypi

    def fail(request: Request, *, timeout: int) -> None:
        raise HTTPError(request.full_url, status, "offline fixture", Message(), None)

    monkeypatch.setattr("release_ci.publisher.urlopen", fail)
    with pytest.raises(PublicationError, match=f"HTTP {status}"):
        fetch_pypi("ledfx-senders", VERSION)


def test_only_pypi_404_is_an_absent_release(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.error import HTTPError
    from urllib.request import Request

    from release_ci.publisher import fetch_pypi

    def missing(request: Request, *, timeout: int) -> None:
        raise HTTPError(request.full_url, 404, "offline fixture", Message(), None)

    monkeypatch.setattr("release_ci.publisher.urlopen", missing)
    assert fetch_pypi("ledfx-senders", VERSION) is None


def test_github_errors_are_fatal_and_do_not_leak_subprocess_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from release_ci.common import run_command

    def fail(
        arguments: list[str], *, capture_output: bool, timeout: int, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            arguments, 1, b"", b"HTTP 404 fixture-secret-value"
        )

    monkeypatch.setattr("release_ci.common.subprocess.run", fail)
    with pytest.raises(PublicationError) as caught:
        run_command(["gh", "api", "repos/example/releases/tags/missing"])
    assert str(caught.value) == "GitHub command failed"


def test_higher_stable_draft_vetoes_latest(
    transaction: tuple[Publisher, Remote],
) -> None:
    publisher, remote = transaction
    original = remote.__call__

    def command(arguments: list[str]) -> bytes:
        if arguments[2] == f"repos/{REPO}/releases":
            return b'[[{"tag_name":"v0.4.0","draft":true,"prerelease":false}]]'
        return original(arguments)

    publisher.command = command
    assert publisher.latest(remote.release) is False
