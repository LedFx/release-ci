# LedFx release-ci

A SHA-pinned composite action and typed Python core for publishing tested Python distributions and finalizing an existing GitHub draft. Optional OCI support promotes LedFx's tested image digests. It does not build packages, create releases, move tags, replace assets, or run consumer code.

Production publication stays in each consumer's existing workflow and environment. The shared action is a step in that job; it is not a reusable workflow. [PyPI does not currently support reusable workflows as Trusted Publishers](https://docs.pypi.org/trusted-publishers/troubleshooting/#reusable-workflows-on-github).

**Start here for native packages:** [maintaining wheel support](docs/native-wheels.md). The native planner derives both the build matrix and required targets before builds from your existing `pyproject.toml`. Python or platform changes then need no second exact wheel-tag matrix. All consumers read their existing `pyproject.toml`. Nonempty target rows select native coverage; absent rows require one `py3-none-any` wheel without a planner job or cibuildwheel installation.

## Caller contract

Keep your build/test gates, same-run artifact downloads, explicit canonical tag-push condition, production environment, narrowly scoped App token, official PyPI and attestation actions, and evidence retention in the caller. Publication jobs must be serialized without cancellation. Use the App token for GitHub finalization so existing `release: published` consumers still receive the event. Grant `id-token: write` only to the caller publication job. Do not pass a PyPI password/token.

| Project configuration | Caller workflow | Existing environment | Distributions |
| --- | --- | --- | --- |
| [ledfx-senders](tests/fixtures/planning/ledfx-senders/pyproject.toml) | `ci.yml` | `pypi` | Generated native coverage + sdist |
| [LedFx](examples/ledfx/pyproject.toml) | `ci.yml` | `production` | One wheel + sdist; four separate frozen GitHub assets; two OCI registries |
| [aubio-ledfx](tests/fixtures/planning/aubio-ledfx/pyproject.toml) | `build.yml` | `pypi` | Generated native coverage + sdist |
| [audio-hotplug](examples/audio-hotplug/pyproject.toml) | `publish.yml` | `pypi` | One wheel + sdist |
| [pyfastnoiselite-ledfx](tests/fixtures/planning/pyfastnoiselite-ledfx/pyproject.toml) | `build.yml` | `pypi` | Generated native coverage with ABI3 reuse + sdist |
| [samplerate-ledfx](tests/fixtures/planning/python-samplerate-ledfx/pyproject.toml) | `ci.yml` | `pypi` | Generated native coverage + sdist; repository is `python-samplerate-ledfx` |

Target rows select native coverage; tests validate coverage against real planner expectations or the constant pure target using retained public release filename/metadata fixtures. Confirm them against the actual source commit before migration. The audio-hotplug fixture is published 0.1.0: 0.2.0 was unavailable when checked, and its PyPI Trusted Publisher was not yet configured. Prepare its migration PR, but an administrator must register the existing `LedFx/audio-hotplug`, `publish.yml`, `pypi` identity before a real release can succeed.

Aubio's manual TestPyPI job remains a separate unchanged caller lane. This production core accepts only canonical tag pushes and has no TestPyPI or arbitrary upload-URL setting. Preserve samplerate's `!cancelled()` plus explicit successful dependency checks: implicit `success()` can suppress a release when a transitive optional job was intentionally skipped. Keep each project's existing version cross-checks (Meson/vcpkg, SCM tags, CMake or package metadata) before builds.

Download selectors also remain local: aubio needs both `wheels-*` and `cibw-sdist`; audio uses `python-package-distributions`; noise/samplerate use `cibw-*`; native uses `sender-dist`. Never download a different run's output or regenerate its binaries. Retain output provenance and configure upload paths so colliding filenames cannot overwrite one another unnoticed.

## Action interface

Use `LedFx/release-ci/actions/release@<reviewed full 40-character commit SHA>`. Moving version tags are for humans, not consumer pins. Include its released `# vX.Y.Z` comment for Renovate tracking. Upgrade all six consumers to the 0.3 pyproject interface and reviewed released SHA/version together.

Required inputs: `phase`, `dist`, `snapshot`. Optional `project` defaults to `.` and points to the caller checkout containing the canonical `pyproject.toml`; use `project: release-tools` when source is checked out separately. Project/distribution/asset/OCI paths must stay inside the caller workspace; `snapshot` is an owned path under `runner.temp`. Native projects require the planning job's full `wheel-plan` JSON on every phase. Pure projects omit it. `GH_TOKEN` is supplied through the step environment. Publication runs on hosted Linux with Python 3.11+ and `gh`; Docker/buildx and registry logins are needed only for OCI. No publication runtime dependencies are installed.

| Phase | Effect | Outputs |
| --- | --- | --- |
| `prepare` | Validate local/remote inputs; freeze owned local snapshot; no remote writes | `already_published`, `pypi_upload` |
| `check-upload` | Recheck immediately before the official PyPI action; no remote writes | `pypi_upload` |
| `promote` | OCI only: verify PyPI/files, create missing matching version/source indexes | `image_digests` JSON map keyed by canonical image name |
| `finalize` | Verify complete publication and provenance; upload missing GitHub assets; optionally update OCI latest; publish existing draft last | `published` |

Boolean outputs are strings `true`/`false` in Actions. Each phase emits only its listed outputs. `image_digests` can be read with `fromJSON(steps.promote.outputs.image_digests)['ghcr.io/ledfx/ledfx']`.

Pure package sequence in the caller job (native callers additionally pass the same generated `wheel-plan` on every phase; see the complete [native workflow](examples/planned-wheels/ci.yml)):

```yaml
# Existing job: needs all required gates, canonical tag-push condition,
# existing environment, id-token/attestations permissions, serialized queue.
# Download same-run tested dist and check out pyproject.toml from the event SHA first.
- id: prepare
  uses: LedFx/release-ci/actions/release@<reviewed-full-SHA>
  env:
    GH_TOKEN: ${{ steps.release-token.outputs.token }}
  with:
    phase: prepare
    project: release-tools
    dist: dist
    snapshot: ${{ runner.temp }}/release-snapshot.json
- id: provenance
  if: steps.prepare.outputs.already_published != 'true'
  uses: actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6 # v4.2.2
  with:
    subject-path: ${{ github.workspace }}/dist/*
    create-storage-record: false
    github-token: ${{ steps.release-token.outputs.token }}
- id: upload
  uses: LedFx/release-ci/actions/release@<reviewed-full-SHA>
  env:
    GH_TOKEN: ${{ steps.release-token.outputs.token }}
  with:
    phase: check-upload
    project: release-tools
    dist: dist
    snapshot: ${{ runner.temp }}/release-snapshot.json
- if: steps.upload.outputs.pypi_upload == 'true'
  uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33 # v1.14.2
  with:
    skip-existing: true # Only after matching remote SHA-256 checks.
- id: finalize
  uses: LedFx/release-ci/actions/release@<reviewed-full-SHA>
  env:
    GH_TOKEN: ${{ steps.release-token.outputs.token }}
  with:
    phase: finalize
    project: release-tools
    dist: dist
    snapshot: ${{ runner.temp }}/release-snapshot.json
```

Always retain the snapshot and `${{ steps.provenance.outputs.bundle-path }}` with the pinned upload-artifact action, including failed finalization. The caller's official PyPI action also supplies PyPI's separate PEP 740 publishing attestation; that does not replace GitHub artifact/OCI provenance verification here.

LedFx additionally attests `assets/*`, logs in to its two registries, calls `promote`, attests each returned image digest with `push-to-registry: true`, then calls `finalize`. Pass the same assets/digest paths to every phase and retain all attestation bundles. The core verifies file and OCI attestations against the caller repository/workflow, tested source SHA, tag ref and hosted runner before finalization. Never change the verifier to trust the shared action repository as the artifact's source.

## Project configuration

`pyproject.toml` is the only configuration source. Read `[project].name` to derive the lowercase wheel stem with punctuation normalized to underscores and the source filename `<stem>-<version>.tar.gz`. The project name need not match its repository (for example `samplerate-ledfx` in `LedFx/python-samplerate-ledfx`). Version comes from the canonical plain or `v`-prefixed tag, with stable/alpha/beta/RC normalization. A static `[project].version` must agree; `dynamic = ["version"]` is supported without executing the backend.

Repository and exact caller workflow come from validated `GITHUB_REPOSITORY` and `GITHUB_WORKFLOW_REF`. The latter must name a workflow under `.github/workflows/` in that repository at the exact tag ref. Provenance verification uses that workflow, repository, tag ref and tested commit. The caller retains its explicit canonical repository/tag-push gate and production environment; credentials remain in the workflow.

A pure library can use its existing project metadata alone. Only these preferences are accepted under `[tool.release-ci]`; unknown keys fail:

| Setting | Default / meaning |
| --- | --- |
| `targets` | Absent: exact `py3-none-any` coverage. Nonempty native rows: generated cibuildwheel plan required. Explicit empty rows fail. |
| `github-distributions` | `true`; attach validated wheel and sdist files to GitHub. |
| `assets` | Empty; optional exact additional versioned filename templates using `{version}` or `{tag_version}`. |
| `oci` | Empty; entries require `image` and `platforms`, with optional `promote-latest` defaulting to `false`. Version tags always use the prefix-free tag version. |

The [LedFx example](examples/ledfx/pyproject.toml) shows four frozen assets and two registries; the [audio example](examples/audio-hotplug/pyproject.toml) needs only project metadata. Merge settings into the existing pyproject rather than copying another metadata document. Native examples are the full maintained [configuration fixtures](tests/fixtures/planning), also used by executable planner tests.

Both kinds use the same discovery, coverage, archive metadata, hash, provenance and retry pipeline. Snapshot identity freezes the full pyproject digest, derived project/version, actual caller workflow/ref, repository/tag/commit, exact filenames/hashes and source-bound wheel plan. Native plans additionally bind the same pyproject digest, installed cibuildwheel version and selected IDs. Pure plans contain the constant `py3-none-any` target without an invented tool version. Changed configuration or caller context vetoes later phases.

Release-ci 0.3 accepts this interface only. Delete the standalone JSON policy, replace every `policy:` action input with `project:` (or omit it for a root checkout), and upgrade the reviewed action pin together. Old JSON CLI inputs and snapshots are rejected; rerun phases with original matching source/artifacts and the full generated native plan. No legacy reader is retained.

Explicit asset names cannot overlap distributions even when GitHub distribution attachment is disabled. Frozen assets are hashed and attested; applications are never unpacked or rebuilt. OCI image names must be explicit `ghcr.io/owner/image` or `docker.io/owner/image`; platforms are `linux/amd64` and/or `linux/arm64`. Receipts retain `docker-amd64.txt`/`docker-arm64.txt`, with one configured immutable `image@sha256:...` source per platform/registry. Docker Hub's short receipt spelling is normalized. Indexes must contain exactly the inspected tested children; layers are never rebuilt.

## Failure and recovery

A pre-existing release-please draft with notes is required. Missing drafts, moved tags, conflicting hashes, unexpected assets, incomplete matrices, invalid metadata, auth/network failures and failed provenance stop publication. There is no fallback release creation or clobber. This intentionally tightens older CLI fallback/clobber behavior. Keep release-please's project-specific version updates and draft/tag configuration.

Matching partial uploads can resume with the original run's exact files; `skip-existing` is allowed only after checksum verification. A matching already-public run verifies without remote writes. An older version can publish its immutable artifacts but cannot promote latest when a higher stable release **or draft** exists. A newer transaction may have already promoted Docker latest before its GitHub finalization failed. An abandoned higher draft can delay latest until a maintainer resolves it. Prereleases never become latest.

Rechecks validate the exact fresh release response used for each write, including its asset list, then rehash local files after legacy asset downloads. Remote services do not offer an atomic compare-and-write for these operations; an administrator can still change remote state after the final validated response. The action does not promise rollback across registries, PyPI and GitHub. A failed staged publication leaves the draft for inspection/retry rather than publishing incomplete output.

Inspect the failed job and retained snapshot/bundles. Fix a configuration prerequisite without replacing artifact bytes or moving the tag, then rerun failed jobs from the original run, for example `gh run rerun RUN_ID --failed --repo OWNER/REPOSITORY`. Do not rebuild the same version and overwrite evidence. Earlier releases without this provenance are not retroactively attested; read-only fixture validation is not proof of a live publishing transaction.

## Development

Publication runtime is standard-library Python; uv manages development tools and the consumer's unprivileged planner/build group. `uv sync --frozen --only-group dev --python 3.12`, `uv run --frozen --only-group dev python -m pytest -q`, and `uv run --frozen --only-group dev prek run --all-files` run the required checks. All maintained Python and stubs are included in strict Pyrefly with explicit Any rejected, plus Ruff annotation rules. Regression sentinels ensure future files cannot escape these gates. CI tests Python 3.11–3.15 with fake network/registry transports and checks the actual composite's unauthorized-call rejection. Tests never publish a release.

The repository's own release-please manages `version.txt`, package metadata/lock and changelog; it does not publish a PyPI package. Release automation uses the existing scoped LedFx App. Consumer build systems, matrices, runtime dependencies and notification jobs remain outside this repository.

Derived from LedFx and ledfx-senders contributors' release tooling. GPL-3.0-or-later; see [LICENSE](LICENSE) and [NOTICE](NOTICE). This does not change any consumer's license.
