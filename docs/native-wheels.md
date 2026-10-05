# Maintaining native wheel support

Put Python selection in `[tool.cibuildwheel]`, platform rows in `[[tool.release-ci.targets]]`, and one cibuildwheel pin in the normal uv `wheel-build` dependency group. The planner enumerates those rows **before any builds** and returns both the Actions matrix and publication expectations. Missing an entire platform's upload fails even when every downloaded wheel is individually valid. No repaired filename list, version count, receipt upload, or ABI3 flag is required.

Use this for cibuildwheel native packages. Pure packages keep schema 1 with `wheel_tags: ["py3-none-any"]`. Existing exact-mode policies remain supported for callers that require exact deployment tags.

## Set up once

1. Add `wheel-build = ["cibuildwheel[uv]==4.2.1"]` to your existing `[dependency-groups]` and update/commit `uv.lock` with `uv add --group wheel-build 'cibuildwheel[uv]==4.2.1'`. The `uv` extra preserves callers currently using cibuildwheel's action with `extras: uv`; it is harmless for other callers. Renovate can discover this ordinary dependency pin. Remove the separate `pypa/cibuildwheel@…` action or `uvx --from cibuildwheel==…` literal.
2. Move your existing wheel matrix rows into `pyproject.toml`. Each row has one explicit `platform`, `arch`, and `runner`. Other keys are short consumer-specific strings, such as `target`, `triplet`, or `macosx_deployment_target`; the planner forwards them unchanged. Rename old `os` fields to `runner` in workflow expressions. Do not move unrelated application/test matrices.
3. Change the release policy to schema 2 and replace its `python.wheel_tags` list with `"wheel_targets": "cibuildwheel"`. Preserve project/stem/sdist, repository/workflow identity, assets and OCI settings. Adopt the reviewed shared commit SHA together with this workflow change.
4. Run the planner in a read-only job after checking out the event SHA and installing uv. Build using its matrix and the same frozen `wheel-build` group. Pass the **full original** `wheel-plan` output to every publication phase. Keep all existing build/test gates and publisher identity.

Here is an ordinary/free-threaded example to merge into an existing native project's configuration (retain its metadata, backend, repair, dependency and test settings):

```toml
[dependency-groups]
wheel-build = ["cibuildwheel[uv]==4.2.1"]

[tool.cibuildwheel]
build = "cp3{11,12,13,14,15}-* cp3{14,15}t-*"
skip = "*-musllinux_* *-win32 *-win_arm64"
enable = ["cpython-prerelease"]
# Keep this project's existing test-command and repair/toolchain options.

[[tool.release-ci.targets]]
platform = "linux"
arch = "x86_64"
runner = "ubuntu-latest"

[[tool.release-ci.targets]]
platform = "windows"
arch = "AMD64"
runner = "windows-latest"

[[tool.release-ci.targets]]
platform = "macos"
arch = "arm64"
runner = "macos-latest"
```

Supported rows: Linux `x86_64`, `aarch64`, `armv7l`, `i686`; macOS `x86_64`, `arm64`; Windows `AMD64`, `ARM64`, `x86`. Architectures are case-sensitive cibuildwheel names. `native`, `auto`, universal2, PyPy, Android and iOS are outside this interface. A row selecting no identifiers fails; remove the row when removing its last supported Python version.

The [complete ledfx-senders integration](../examples/planned-wheels/ci.yml) shows planning, builds, sdist smoke testing, same-run downloads, provenance, snapshot retention, and all three package publication phases. Use its [schema 2 policy](../examples/planned-wheels/release-policy.json), the [full native configuration](../tests/fixtures/planning/ledfx-senders/pyproject.toml), and update/commit its uv lock. Replace `REVIEWED_SHARED_SHA` and `vNEXT_RELEASE` with the same reviewed release SHA/version on every plan/release step, and configure the consumer's existing `pypi` environment and scoped App variables/secrets. That example preserves the existing ledfx-senders release, lint, Rust, installed-wheel, sdist and aggregate gates. It is for that source tree, rather than a standalone package. Copy the [updated regression guard](../examples/planned-wheels/test_shared_release.py) to `ci/test_shared_release.py` so its former schema 1 wheel-count assertions accept the planned policy. These configuration, lock, workflow, policy and guard changes are one migration; the workflow alone cannot be dropped into unchanged source.

## Make a support change

- **Add/remove Python:** edit cibuildwheel's `build`/`skip`/`enable` selectors. Keep `project.requires-python` consistent with the package's actual runtime support; cibuildwheel also reads it. A newly selected version appears in both the builds and plan. No publication tag list or count needs editing. Free-threaded identifiers use `cp314t`, while their wheels use Python tag `cp314` and ABI `cp314t`.
- **Add/remove a platform:** add/remove one target row, preserving its toolchain metadata. The matrix and coverage change together. Set up any platform-specific toolchain/QEMU steps in the caller when needed; selection cannot create those tools.
- **Update cibuildwheel:** update the single `wheel-build` dependency and lock, preview the new identifiers, then run ordinary build/repair tests. A new Python release must first be known by the locked cibuildwheel version. Do not add another workflow version pin.

All selection belongs in checked-in `pyproject.toml`: do not supply `CIBW_BUILD`, `CIBW_SKIP`, `CIBW_ENABLE`, `CIBW_ARCHS`, `CIBW_PROJECT_REQUIRES_PYTHON`, their platform variants, `--only`, or alternate build configurations. The planner rejects selector environment variables and strips all other `CIBW_*` variables from its child. Build commands use `--config-file pyproject.toml --platform "$PLATFORM" --archs "$ARCH"`, so a Linux planner and the actual platform job enumerate the same configuration. Caller toolchain variables such as vcpkg/MACOSX_DEPLOYMENT_TARGET remain build concerns; preserve them. The print-identifiers command does not build, run consumer hooks, or invoke repair/test commands.

Preview locally from the consumer root, using a separate checkout of the shared action:

```bash
uv run --frozen --only-group wheel-build python /path/to/release-ci/plan_entrypoint.py \
  --source-sha "$(git rev-parse HEAD)"
```

The printed JSON has `matrix: {"include": […]}` and `wheel-plan: {"schema_version": 1, "source_sha": …, "cibuildwheel": …, "targets": […]}`. In Actions these are compact single-line step outputs. The `source-sha` action input defaults to `github.sha`; use the tested commit, including the merge commit for PR builds. Keep the same checked-out commit/configuration/lock for planning and builds. The CLI checks that checkout HEAD matches the requested source SHA. No planner or uv installation runs in the privileged publication job.

## ABI3 and repaired tags

For a limited-API project such as pyfastnoiselite, retain the backend's ABI3 configuration and select every supported interpreter, including its baseline:

```toml
[tool.cibuildwheel]
build = ["cp311-*", "cp312-*", "cp313-*", "cp314-*", "cp315-*"]
skip = ["*-win32", "*-manylinux_i686", "*-musllinux_i686"]
# Existing backend builds cp311-abi3; cibuildwheel tests/reuses it on later CPython.
```

A `cp311-abi3` wheel covers selected GIL-enabled CPython 3.11 and later for its family/architecture only. The baseline itself must be selected. It never covers `cp314t` or `cp315t`; those require their own ABI wheels. No second per-version ABI3 table is required. One filename with compressed `manylinux_2_17_x86_64.manylinux2014_x86_64` compatibility tags is one output covering one family/architecture. Linux manylinux and musllinux remain distinct. macOS deployment minima normalize to macOS plus architecture; Windows architectures remain distinct.

Every planned ID must be covered exactly once and every wheel must cover at least one ID. Unknown platform shapes, mixed families/architectures, invalid Python/ABI shapes, and overlapping ABI3/per-interpreter outputs fail. Filename and WHEEL metadata must agree, project/version must match, and all files must be ordinary files. The exact sdist is still required. Explicit GitHub asset names cannot collide with discovered distributions even when distribution attachment is disabled.

Target mode checks completeness, **not deployment floors**. Cibuildwheel, auditwheel/delocate/delvewheel, ABI auditing and the consumer's native/installed-wheel tests own dependency compatibility and deployment minima. Publication validates tag shape and coverage, then freezes actual filenames and bytes. It does not predict auditwheel aliases or reject a macOS minimum merely for differing from a previous release. Use schema 1 exact mode when that exact-tag guarantee is required.

## Diagnose a failure and retry

`Missing wheel targets: cp311-macosx_arm64, …` means the pre-build plan lacks covering output, including a missing entire upload job. Check the failed/skipped build and its same-run artifact selector; do not shrink the plan to match downloaded files.

`Overlapping target cp312-win_amd64: …` means two outputs cover the same selected target, for example both `cp311-abi3` and `cp312-cp312`. Check the backend's ABI3 settings or duplicate uploads. `Unexpected wheel has no planned target` means an extra interpreter, wrong ABI or platform was built. `ABI3 baseline is not selected` means the baseline was omitted from cibuildwheel selection. Inspect the printed plan before changing configuration.

The full plan is frozen in the existing immutable snapshot alongside exact filenames/hashes. A changed plan, source SHA, policy, archive, remote file or provenance prevents a write. Retain the snapshot and attestation bundles even on failure. Fix external configuration without replacing artifact bytes/moving the tag, then rerun failed jobs from the original run (`gh run rerun RUN_ID --failed --repo OWNER/REPOSITORY`). Reuse its original planning output. A new source/configuration requires a new version/run; regenerating a smaller plan cannot repair a partial publication. Existing exact-mode retry, provenance, immutable draft ID and finalization-last checks remain unchanged.

## Apply to the current consumers

The fixture tests execute real cibuildwheel enumeration on Linux against disposable configuration copies. Retained filename fixtures prove current coverage: senders 35 wheels, Aubio 25, samplerate 25, and noise 45 interpreter IDs covered by nine ABI3 wheels. These fixed numbers describe controlled fixtures; they are not future support policy. No native binaries were rebuilt for these proofs.

| Consumer | Migration detail |
| --- | --- |
| ledfx-senders | Move its five native rows, retaining `target` artifact labels; keep Rust installation, pinned Linux images and macOS overrides. Replace CI's hard-coded `35`/free-threaded `10` wheel-count assertions with plan coverage, not updated counts. Its separate installed-wheel runtime matrix currently repeats Python/OS coverage: treat it as independent smoke-test coverage and update it deliberately when changing that test contract; the planner guarantees cibuildwheel tests and wheel publication coverage, not that separate matrix. Do not claim that matrix automatically expands. |
| aubio-ledfx | Move its five JSON rows with `triplet`/`macosx_deployment_target` string metadata. Filter `fromJSON(plan.matrix).include` using the existing PR platform/mid-stack logic instead of the old literal JSON; tag releases must use all rows and the full unfiltered plan. Keep vcpkg setup, Windows environment/path handling, release version checks, aggregate gate and separate manual TestPyPI lane. |
| pyfastnoiselite-ledfx | Move six rows. Preserve armv7l QEMU setup, `test-skip`, the ABI3 backend and its reuse/tests. Replace cibuildwheel action plus `extras: uv` with the frozen group command. Nine ABI3 wheels cover both libc families across three Linux architectures, Windows and two Macs. Keep its sdist-built smoke wheel outside publication output. |
| python-samplerate-ledfx | Move five rows with explicit architecture; the same CLI architecture now governs plan/build instead of implicit native defaults. Keep the `build[uv]` frontend, test group, sdist smoke test, and optional gates. Preserve `!cancelled()` and explicit successful required dependencies so intentionally skipped optional jobs do not suppress tag publication. |
| LedFx / audio-hotplug | Keep schema 1 and one pure wheel. Application/runtime/frozen/Docker/test matrices do not enter this planner. |

Adopt this feature after the shared implementation is reviewed and released/pinned; the existing six consumer PRs stay on released 0.2.0 until that follow-up. Publisher repository/workflow/environment identity and PyPI registrations do not change. The copied [configuration fixtures](../tests/fixtures/planning) record their inspected source commits; they are test data, not migration edits to production branches.

When updating existing consumer regression tests, replace the block that reads `python.wheel_tags` and asserts a fixed wheel/free-threaded count with schema 2 and wiring checks:

```python
assert policy["schema_version"] == 2
assert policy["python"]["wheel_targets"] == "cibuildwheel"
assert "wheel_tags" not in policy["python"]
assert job.count("wheel-plan: ${{ needs.plan.outputs.wheel-plan }}") == 3
assert "matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}" in workflow
```

Keep the surrounding repository/version/sdist, publisher pin, gate, provenance and retry-order assertions. Retain the released `# vX.Y.Z` pin comment and check the plan/release steps use that same SHA/version. The shared suite exercises real selection and missing/overlapping coverage; callers need no second support list or count to repeat it.
