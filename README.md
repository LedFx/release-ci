# LedFx release-ci

A SHA-pinned composite action and typed Python core for publishing tested Python distributions and finalizing an existing GitHub draft. Optional OCI support promotes LedFx's tested image digests. It does not build packages, create releases, move tags, replace assets, or run consumer code.

Production publication stays in each consumer's existing workflow and environment. The shared action is a step in that job; it is not a reusable workflow. [PyPI does not currently support reusable workflows as Trusted Publishers](https://docs.pypi.org/trusted-publishers/troubleshooting/#reusable-workflows-on-github).

## Caller contract

Keep your build/test gates, same-run artifact downloads, explicit canonical tag-push condition, production environment, narrowly scoped App token, official PyPI and attestation actions, and evidence retention in the caller. Publication jobs must be serialized without cancellation. Use the App token for GitHub finalization so existing `release: published` consumers still receive the event. Grant `id-token: write` only to the caller publication job. Do not pass a PyPI password/token.

| Example policy | Caller workflow | Existing environment | Distributions |
| --- | --- | --- | --- |
| [ledfx-senders](examples/ledfx-senders.json) | `ci.yml` | `pypi` | 35 wheels + sdist |
| [LedFx](examples/ledfx.json) | `ci.yml` | `production` | One wheel + sdist; four separate frozen GitHub assets; two OCI registries |
| [aubio-ledfx](examples/aubio-ledfx.json) | `build.yml` | `pypi` | 25 wheels + sdist |
| [audio-hotplug](examples/audio-hotplug.json) | `publish.yml` | `pypi` | One wheel + sdist |
| [pyfastnoiselite-ledfx](examples/pyfastnoiselite-ledfx.json) | `build.yml` | `pypi` | Nine ABI3 wheels + sdist |
| [samplerate-ledfx](examples/samplerate-ledfx.json) | `ci.yml` | `pypi` | 25 wheels + sdist; repository is `python-samplerate-ledfx` |

Policies are explicit examples matched against retained public release filename/metadata fixtures, not permission to change a build matrix. Confirm them against the actual source commit before migration. The audio-hotplug fixture is published 0.1.0: 0.2.0 was unavailable when checked, and its PyPI Trusted Publisher was not yet configured. Prepare its migration PR, but an administrator must register the existing `LedFx/audio-hotplug`, `publish.yml`, `pypi` identity before a real release can succeed.

Aubio's manual TestPyPI job remains a separate unchanged caller lane. This production core accepts only canonical tag pushes and has no TestPyPI or arbitrary upload-URL setting. Preserve samplerate's `!cancelled()` plus explicit successful dependency checks: implicit `success()` can suppress a release when a transitive optional job was intentionally skipped. Keep each project's existing version cross-checks (Meson/vcpkg, SCM tags, CMake or package metadata) before builds.

Download selectors also remain local: aubio needs both `wheels-*` and `cibw-sdist`; audio uses `python-package-distributions`; noise/samplerate use `cibw-*`; native uses `sender-dist`. Never download a different run's output or regenerate its binaries. Retain output provenance and configure upload paths so colliding filenames cannot overwrite one another unnoticed.

## Action interface

Use `LedFx/release-ci/actions/release@<reviewed full 40-character commit SHA>`. Moving version tags are for humans, not consumer pins. A reviewed commit can be referenced before an action version is released.

Required inputs: `phase`, `policy`, `dist`, `snapshot`. `policy` and `dist` are paths in the caller workspace; `snapshot` is an owned path under `runner.temp`. Optional `assets` and `docker-digests` are caller workspace paths and must match the policy. `GH_TOKEN` is supplied explicitly through the step environment. Runtime: hosted Linux, Python 3.11+, `gh`; Docker/buildx and registry logins only for OCI policies. No runtime dependency installation occurs.

| Phase | Effect | Outputs |
| --- | --- | --- |
| `prepare` | Validate local/remote inputs; freeze owned local snapshot; no remote writes | `already_published`, `pypi_upload` |
| `check-upload` | Recheck immediately before the official PyPI action; no remote writes | `pypi_upload` |
| `promote` | OCI only: verify PyPI/files, create missing matching version/source indexes | `image_digests` JSON map keyed by canonical image name |
| `finalize` | Verify complete publication and provenance; upload missing GitHub assets; optionally update OCI latest; publish existing draft last | `published` |

Boolean outputs are strings `true`/`false` in Actions. Each phase emits only its listed outputs. `image_digests` can be read with `fromJSON(steps.promote.outputs.image_digests)['ghcr.io/ledfx/ledfx']`.

Typical package sequence in the caller job:

```yaml
# Existing job: needs all required gates, canonical tag-push condition,
# existing environment, id-token/attestations permissions, serialized queue.
# Download same-run tested dist and check out policy from the event SHA first.
- id: prepare
  uses: LedFx/release-ci/actions/release@<reviewed-full-SHA>
  env:
    GH_TOKEN: ${{ steps.release-token.outputs.token }}
  with:
    phase: prepare
    policy: release-tools/.github/release-policy.json
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
    policy: release-tools/.github/release-policy.json
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
    policy: release-tools/.github/release-policy.json
    dist: dist
    snapshot: ${{ runner.temp }}/release-snapshot.json
```

Always retain the snapshot and `${{ steps.provenance.outputs.bundle-path }}` with the pinned upload-artifact action, including failed finalization. The caller's official PyPI action also supplies PyPI's separate PEP 740 publishing attestation; that does not replace GitHub artifact/OCI provenance verification here.

LedFx additionally attests `assets/*`, logs in to its two registries, calls `promote`, attests each returned image digest with `push-to-registry: true`, then calls `finalize`. Pass the same assets/digest paths to every phase and retain all attestation bundles. The core verifies file and OCI attestations against the caller repository/workflow, tested source SHA, tag ref and hosted runner before finalization. Never change the verifier to trust the shared action repository as the artifact's source.

## Policy schema 1

All fields are required, including empty `oci`/asset lists; unknown or duplicate keys fail. See the complete JSON examples. `repository` and `workflow` must match event identity. `tag_prefix` is `v` or empty. Versions accept three-component stable versions, PEP 440 `aN`, `bN`, `rcN`, and corresponding `-alpha.N`, `-beta.N`, `-rc.N` tags; local/dev/post releases and ambiguous leading zeros are rejected.

`python.project` is compared using standard normalized project spelling. `wheel_stem` preserves exact filenames; `wheel_tags` is an explicit list, including compressed platform tags. The expanded WHEEL metadata tag set must match the filename. `sdist` is an exact name template. Every expected wheel and the sdist is required; no extra files or symlinks. Archive metadata must match project and version. Package licenses are not rewritten.

`github_assets.distributions` selects whether distribution files are attached to GitHub; `files` lists other exact versioned filenames. Only `{version}` (normalized Python version) and `{tag_version}` (prefix removed) placeholders are accepted. Frozen assets are hashed and attested; the core does not unpack or rebuild applications.

Each optional `oci` entry has an explicit `ghcr.io/owner/image` or `docker.io/owner/image`, `platforms` (`linux/amd64`, `linux/arm64`), `version_tag: "{tag_version}"`, and `promote_latest`. Receipts retain existing `docker-amd64.txt`/`docker-arm64.txt` format: one configured immutable `image@sha256:...` source per platform/registry. Docker Hub's short `owner/image` receipt spelling is normalized. Version and source-SHA indexes must contain exactly the inspected tested children. The core never rebuilds layers.

## Failure and recovery

A pre-existing release-please draft with notes is required. Missing drafts, moved tags, conflicting hashes, unexpected assets, incomplete matrices, invalid metadata, auth/network failures and failed provenance stop publication. There is no fallback release creation or clobber. This intentionally tightens older CLI fallback/clobber behavior. Keep release-please's project-specific version updates and draft/tag configuration.

Matching partial uploads can resume with the original run's exact files; `skip-existing` is allowed only after checksum verification. A matching already-public run verifies without remote writes. An older version can publish its immutable artifacts but cannot promote latest when a higher stable release **or draft** exists. A newer transaction may have already promoted Docker latest before its GitHub finalization failed. An abandoned higher draft can delay latest until a maintainer resolves it. Prereleases never become latest.

Rechecks validate the exact fresh release response used for each write, including its asset list, then rehash local files after legacy asset downloads. Remote services do not offer an atomic compare-and-write for these operations; an administrator can still change remote state after the final validated response. The action does not promise rollback across registries, PyPI and GitHub. A failed staged publication leaves the draft for inspection/retry rather than publishing incomplete output.

Inspect the failed job and retained snapshot/bundles. Fix a configuration prerequisite without replacing artifact bytes or moving the tag, then rerun failed jobs from the original run, for example `gh run rerun RUN_ID --failed --repo OWNER/REPOSITORY`. Do not rebuild the same version and overwrite evidence. Earlier releases without this provenance are not retroactively attested; read-only fixture validation is not proof of a live publishing transaction.

## Development

Runtime is standard-library Python; uv manages only development tools. `uv sync --frozen --only-group dev --python 3.12`, `uv run --frozen --only-group dev python -m pytest -q`, and `uv run --frozen --only-group dev prek run --all-files` run the required checks. All maintained Python and stubs are included in strict Pyrefly with explicit Any rejected, plus Ruff annotation rules. Regression sentinels ensure future files cannot escape these gates. CI tests Python 3.11–3.15 with fake network/registry transports and checks the actual composite's unauthorized-call rejection. Tests never publish a release.

The repository's own release-please manages `version.txt`, package metadata/lock and changelog; it does not publish a PyPI package. Release automation uses the existing scoped LedFx App. Consumer build systems, matrices, runtime dependencies and notification jobs remain outside this repository.

Derived from LedFx and ledfx-senders contributors' release tooling. GPL-3.0-or-later; see [LICENSE](LICENSE) and [NOTICE](NOTICE). This does not change any consumer's license.
