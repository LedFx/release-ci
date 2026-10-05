"""Immutable release transactions; no release/tag/asset replacement."""

import hashlib
import json
import re
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .archives import distributions, files
from .common import (
    Command,
    Fetch,
    Hashes,
    PublicationError,
    array,
    decode,
    digest,
    flag,
    positive_id,
    run_command,
    string,
    table,
)
from .config import Config, caller_workflow, parse_version
from .oci import OCI
from .targets import WheelPlan


def fetch_pypi(project: str, version: str) -> object:
    request = Request(
        f"https://pypi.org/pypi/{quote(project, safe='')}/{version}/json",
        headers={"User-Agent": "LedFx-release-ci"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return decode(response.read())
    except HTTPError as error:
        if error.code == 404:
            return None
        raise PublicationError(f"PyPI returned HTTP {error.code}") from None
    except (URLError, TimeoutError, OSError):
        raise PublicationError("PyPI request failed or timed out") from None


class Publisher:
    def __init__(
        self,
        dist: Path,
        snapshot: Path,
        repository: str,
        tag: str,
        sha: str,
        *,
        project: Path,
        workflow_ref: str,
        assets: Path | None = None,
        docker_digests: Path | None = None,
        wheel_plan: str | None = None,
        command: Command = run_command,
        fetch: Fetch | None = None,
    ) -> None:
        self.config_path = project / "pyproject.toml"
        self.config_digest = digest(self.config_path)
        self.config = Config.load(project)
        if digest(self.config_path) != self.config_digest:
            raise PublicationError("pyproject.toml changed while loading configuration")
        self.workflow = caller_workflow(repository, workflow_ref, tag)
        self.workflow_ref = workflow_ref
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise PublicationError("Invalid source SHA")
        self.parsed = parse_version(tag)
        if (
            self.config.static_version is not None
            and parse_version(self.config.static_version).package != self.parsed.package
        ):
            raise PublicationError("Static project version differs from release tag")
        if self.config.wheel_targets == "pure":
            if wheel_plan is not None:
                raise PublicationError(
                    "Pure wheel config does not accept a pre-build wheel plan"
                )
            self.wheel_plan = WheelPlan.pure(sha)
        else:
            if wheel_plan is None:
                raise PublicationError(
                    "Cibuildwheel config requires a pre-build wheel plan"
                )
            self.wheel_plan = WheelPlan.load(decode(wheel_plan), sha)
            if self.wheel_plan.pyproject_sha256 != self.config_digest:
                raise PublicationError(
                    "Wheel plan differs from pyproject.toml configuration"
                )
            self.wheel_plan.validate_rows(self.config.build_rows)
        self.dist, self.snapshot, self.assets = dist, snapshot, assets
        self.repo, self.tag, self.sha = repository, tag, sha
        self.version = self.parsed.package
        self.command = command
        self.fetch = fetch or (lambda version: fetch_pypi(self.config.project, version))
        self.oci = OCI(self.config.oci, docker_digests, self.parsed, sha, command)

    def api(self, endpoint: str, *options: str) -> object:
        return decode(
            self.command(["gh", "api", f"repos/{self.repo}/{endpoint}", *options])
        )

    def release(self) -> dict[str, object]:
        # The tag endpoint returns published releases only. Authenticated lists
        # include drafts; scan every page and reject ambiguous exact-tag matches.
        matches = [
            table(value)
            for page in array(self.api("releases", "--paginate", "--slurp"))
            for value in array(page)
            if table(value).get("tag_name") == self.tag
        ]
        if len(matches) != 1:
            raise PublicationError("Release tag missing or ambiguous")
        identifier = positive_id(matches[0].get("id"))
        # Refresh by immutable ID, rather than using stale listing state to write.
        release = table(self.api(f"releases/{identifier}"))
        if positive_id(release.get("id")) != identifier:
            raise PublicationError("Release ID changed during discovery")
        if (
            release.get("tag_name") != self.tag
            or not string(release.get("body")).strip()
        ):
            raise PublicationError("Release tag/notes missing or different")
        flag(release.get("draft"))
        prerelease = flag(release.get("prerelease"))
        if prerelease != (self.parsed.stable is None):
            raise PublicationError("Release prerelease flag does not match its version")
        array(release.get("assets"))
        ref = table(self.api(f"git/ref/tags/{self.tag}"))
        for _ in range(10):
            obj = table(ref.get("object"))
            sha = string(obj.get("sha"))
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                break
            if obj.get("type") == "commit":
                if sha != self.sha:
                    raise PublicationError(
                        "Remote tag moved away from the tested source SHA"
                    )
                return release
            if obj.get("type") != "tag":
                break
            ref = table(self.api(f"git/tags/{sha}"))
        raise PublicationError("Remote tag does not resolve to the tested commit")

    def local_inputs(self) -> Hashes:
        if (
            self.config_path.is_symlink()
            or digest(self.config_path) != self.config_digest
        ):
            raise PublicationError("Config changed during transaction")
        return distributions(self.dist, self.config, self.parsed, self.wheel_plan)

    def asset_inputs(self) -> Hashes:
        result = self.local_inputs() if self.config.github_distributions else {}
        extras = files(self.assets, self.config.asset_names(self.parsed))
        if set(result) & set(extras):
            raise PublicationError("Duplicate GitHub asset filename")
        return result | extras

    def asset_path(self, name: str) -> Path:
        return (
            self.dist / name if name in self.local_inputs() else self.assets_path(name)
        )

    def assets_path(self, name: str) -> Path:
        if self.assets is None:
            raise PublicationError("Asset directory required")
        return self.assets / name

    def verify_assets(
        self, release: Mapping[str, object], hashes: Hashes, *, complete: bool = False
    ) -> set[str]:
        found: set[str] = set()
        for value in array(release.get("assets")):
            asset = table(value)
            name = string(asset.get("name"))
            if name in found or name not in hashes:
                raise PublicationError("Unexpected or duplicate GitHub assets")
            identifier = positive_id(asset.get("id"))
            actual = asset.get("digest")
            if actual is None:
                data = self.command(
                    [
                        "gh",
                        "api",
                        f"repos/{self.repo}/releases/assets/{identifier}",
                        "-H",
                        "Accept: application/octet-stream",
                    ]
                )
                actual = "sha256:" + hashlib.sha256(data).hexdigest()
            if actual != "sha256:" + hashes[name]:
                raise PublicationError("GitHub asset checksum conflict")
            found.add(name)
        missing = set(hashes) - found
        if missing and (complete or not release["draft"]):
            raise PublicationError("GitHub assets incomplete")
        return missing

    def verify_pypi(self, hashes: Hashes, *, complete: bool = False) -> bool:
        for attempt in range(3 if complete else 1):
            response = self.fetch(self.version)
            files = [] if response is None else array(table(response).get("urls"))
            found: set[str] = set()
            for value in files:
                item = table(value)
                name = string(item.get("filename"))
                if (
                    name not in hashes
                    or name in found
                    or table(item.get("digests")).get("sha256") != hashes[name]
                ):
                    raise PublicationError(
                        "PyPI filenames/checksums conflict with tested artifacts"
                    )
                found.add(name)
            if found == set(hashes):
                return True
            if not complete:
                return False
            if attempt < 2:
                time.sleep(2)
        raise PublicationError("PyPI distributions incomplete")

    def latest(self, release: Mapping[str, object]) -> bool:
        if release["prerelease"] or self.parsed.stable is None:
            return False
        for page in array(self.api("releases", "--paginate", "--slurp")):
            for value in array(page):
                other = table(value)
                if flag(other.get("prerelease")):
                    continue
                # Higher stable drafts may already have promoted OCI latest.
                tag = string(other.get("tag_name"))
                try:
                    version = parse_version(tag)
                except PublicationError:
                    continue
                if version.stable is not None and version.stable > self.parsed.stable:
                    return False
        return True

    def identity(
        self, release: Mapping[str, object], hashes: Hashes
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "repository": self.repo,
            "tag": self.tag,
            "sha": self.sha,
            "release_id": release["id"],
            "body_sha256": hashlib.sha256(string(release["body"]).encode()).hexdigest(),
            "prerelease": release["prerelease"],
            "hashes": hashes,
            "assets": self.asset_inputs(),
            "pyproject_sha256": digest(self.config_path),
            "workflow": self.workflow,
            "workflow_ref": self.workflow_ref,
            "project": self.config.project,
            "version": self.version,
            "oci_sources": self.oci.sources(),
        }
        result["wheel_plan"] = self.wheel_plan.value()
        return result

    def load_snapshot(self) -> tuple[dict[str, object], Hashes, dict[str, object]]:
        snapshot = table(decode(self.snapshot.read_text()))
        hashes, release = self.local_inputs(), self.release()
        if any(
            snapshot.get(k) != value
            for k, value in self.identity(release, hashes).items()
        ):
            raise PublicationError(
                "Publication inputs no longer match the frozen snapshot"
            )
        published = flag(snapshot.get("already_published"))
        flag(snapshot.get("latest"))
        if published and release["draft"]:
            raise PublicationError("Published release unexpectedly became a draft")
        return snapshot, hashes, release

    def recheck(
        self, snapshot: Mapping[str, object], hashes: Hashes, *, complete: bool = False
    ) -> dict[str, object]:
        release = self.release()
        self.verify_assets(release, self.asset_inputs(), complete=complete)
        # Remote legacy-asset downloads can take time. Hash after those reads.
        current, current_hashes, release = self.load_snapshot()
        if current != snapshot or current_hashes != hashes:
            raise PublicationError("Publication snapshot changed before a write")
        # Validate the exact fresh response whose draft flag controls the write.
        self.verify_assets(release, self.asset_inputs(), complete=complete)
        # That validation can download legacy assets too. Rehash locally without
        # replacing the validated release response with another unchecked GET.
        if (
            table(decode(self.snapshot.read_text())) != snapshot
            or self.local_inputs() != hashes
            or self.asset_inputs() != snapshot.get("assets")
            or digest(self.config_path) != snapshot.get("pyproject_sha256")
            or self.oci.sources() != snapshot.get("oci_sources")
            or self.wheel_plan.value() != snapshot.get("wheel_plan")
        ):
            raise PublicationError("Publication snapshot changed before a write")
        return release

    def prepare(self) -> dict[str, bool]:
        hashes, release = self.local_inputs(), self.release()
        self.verify_assets(release, self.asset_inputs())
        complete = self.verify_pypi(hashes, complete=not flag(release["draft"]))
        self.oci.verify(self.oci.source_children(), complete=not flag(release["draft"]))
        snapshot = {
            **self.identity(release, hashes),
            "already_published": not flag(release["draft"]),
            "latest": self.latest(release),
            "oci_children": self.oci.source_children(),
        }
        if self.snapshot.exists():
            existing, _, _ = self.load_snapshot()
            snapshot = existing
        else:
            with self.snapshot.open("x") as handle:
                json.dump(snapshot, handle, indent=2, sort_keys=True)
        release = self.recheck(snapshot, hashes)
        if not release["draft"]:
            complete = self.verify_pypi(hashes, complete=True)
            self.verify_attestations(hashes)
            self.oci.verify_attestations(
                self.oci.source_children(), self.repo, self.workflow, self.tag
            )
        return {
            "already_published": not flag(release["draft"]),
            "pypi_upload": not complete,
        }

    def check_upload(self) -> dict[str, bool]:
        snapshot, hashes, release = self.load_snapshot()
        complete = self.verify_pypi(hashes, complete=not flag(release["draft"]))
        release = self.recheck(snapshot, hashes)
        if not release["draft"]:
            self.verify_pypi(hashes, complete=True)
            return {"pypi_upload": False}
        return {"pypi_upload": not complete}

    def verify_attestations(self, hashes: Hashes) -> None:
        targets = {str(self.dist / name) for name in hashes}
        targets.update(str(self.asset_path(name)) for name in self.asset_inputs())
        for path in sorted(targets):
            self.command(
                [
                    "gh",
                    "attestation",
                    "verify",
                    path,
                    "--repo",
                    self.repo,
                    "--signer-workflow",
                    f"{self.repo}/{self.workflow}",
                    "--source-digest",
                    self.sha,
                    "--source-ref",
                    f"refs/tags/{self.tag}",
                    "--deny-self-hosted-runners",
                ]
            )

    def finalize(self) -> dict[str, bool]:
        snapshot, hashes, release = self.load_snapshot()
        self.verify_assets(release, self.asset_inputs())
        self.verify_pypi(hashes, complete=True)
        self.verify_attestations(hashes)
        attested_digests = self.oci.verify_attestations(
            self.oci.checked_children(snapshot),
            self.repo,
            self.workflow,
            self.tag,
        )
        release = self.recheck(snapshot, hashes)
        for name in sorted(self.verify_assets(release, self.asset_inputs())):
            release = self.recheck(snapshot, hashes)
            missing = self.verify_assets(release, self.asset_inputs())
            if not release["draft"] or name not in missing:
                continue
            # Address the already-verified immutable ID; never upload by a moving tag.
            identifier = positive_id(snapshot["release_id"])
            endpoint = f"https://uploads.github.com/repos/{self.repo}/releases/{identifier}/assets?name={quote(name, safe='')}"
            self.command(
                [
                    "gh",
                    "api",
                    endpoint,
                    "--method",
                    "POST",
                    "-H",
                    "Content-Type: application/octet-stream",
                    "-H",
                    f"Content-Length: {self.asset_path(name).stat().st_size}",
                    "--input",
                    str(self.asset_path(name)),
                ]
            )
        release = self.recheck(snapshot, hashes, complete=True)
        self.verify_pypi(hashes, complete=True)
        latest = flag(snapshot["latest"]) and self.latest(release)
        release = self.recheck(snapshot, hashes, complete=True)
        if release["draft"] and latest:
            self.oci.promote_latest(
                self.oci.checked_children(snapshot),
                attested_digests,
                lambda: self.recheck(snapshot, hashes, complete=True),
                lambda: flag(snapshot["latest"]) and self.latest(self.release()),
            )
            latest = flag(snapshot["latest"]) and self.latest(self.release())
            release = self.recheck(snapshot, hashes, complete=True)
        if self.oci.digests(self.oci.checked_children(snapshot)) != attested_digests:
            raise PublicationError("OCI version descriptor changed after attestation")
        release = self.recheck(snapshot, hashes, complete=True)
        if release["draft"]:
            self.command(
                [
                    "gh",
                    "api",
                    f"repos/{self.repo}/releases/{snapshot['release_id']}",
                    "--method",
                    "PATCH",
                    "-F",
                    "draft=false",
                    "-f",
                    f"make_latest={str(latest).lower()}",
                ]
            )
        public = self.release()
        if public["draft"] or self.identity(public, hashes) != self.identity(
            release, hashes
        ):
            raise PublicationError("Published release identity did not verify")
        self.verify_assets(public, self.asset_inputs(), complete=True)
        return {"published": True}

    def promote(self) -> dict[str, str]:
        snapshot, hashes, _release = self.load_snapshot()
        self.verify_pypi(hashes, complete=True)
        self.verify_attestations(hashes)
        return self.oci.promote(
            self.oci.checked_children(snapshot),
            lambda: self.recheck(snapshot, hashes),
        )
