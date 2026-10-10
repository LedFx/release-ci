# Changelog

## [0.4.2](https://github.com/LedFx/release-ci/compare/v0.4.1...v0.4.2) (2026-10-10)


### Bug Fixes

* allow a version-scope consumer with only simple version files ([#19](https://github.com/LedFx/release-ci/issues/19)) ([ceb1e80](https://github.com/LedFx/release-ci/commit/ceb1e8006cabff918f6cf40229c8113f3df47043))

## [0.4.1](https://github.com/LedFx/release-ci/compare/v0.4.0...v0.4.1) (2026-10-10)


### Bug Fixes

* put the action root on PYTHONPATH for the version-scope entrypoint ([#17](https://github.com/LedFx/release-ci/issues/17)) ([88e9499](https://github.com/LedFx/release-ci/commit/88e94998ef70ce28adeadfa5259f48ab40c61494))

## [0.4.0](https://github.com/LedFx/release-ci/compare/v0.3.1...v0.4.0) (2026-10-10)


### Features

* version_scope detector and version-scope composite action ([#15](https://github.com/LedFx/release-ci/issues/15)) ([1a7e0ba](https://github.com/LedFx/release-ci/commit/1a7e0ba4092b46a0f0e6d15a82f0d19c0a0e98fb))

## [0.3.1](https://github.com/LedFx/release-ci/compare/v0.3.0...v0.3.1) (2026-10-05)


### Bug Fixes

* discover draft releases before publication ([#12](https://github.com/LedFx/release-ci/issues/12)) ([089c9fe](https://github.com/LedFx/release-ci/commit/089c9fe155c435e3da8fac051a697eee7645e386))
* isolate PyPI upload sidecars from verified distributions ([#14](https://github.com/LedFx/release-ci/issues/14)) ([f88cea3](https://github.com/LedFx/release-ci/commit/f88cea3e61dee373295f8c9c895e1fe5a5a4ebc4))

## [0.3.0](https://github.com/LedFx/release-ci/compare/v0.2.0...v0.3.0) (2026-10-05)


### ⚠ BREAKING CHANGES

* derive release configuration from pyproject and caller context ([#10](https://github.com/LedFx/release-ci/issues/10))

### Features

* derive release configuration from pyproject and caller context ([#10](https://github.com/LedFx/release-ci/issues/10)) ([6f73d2d](https://github.com/LedFx/release-ci/commit/6f73d2da4182f7b664023646a5e3380764574adf))

## [0.2.0](https://github.com/LedFx/release-ci/compare/v0.1.0...v0.2.0) (2026-10-05)


### Features

* share verified release publication across the LedFx fleet ([6508bc8](https://github.com/LedFx/release-ci/commit/6508bc8af65d783fee62778ca62acfab1ce5c88e))
* share verified release transactions through a composite action ([adea166](https://github.com/LedFx/release-ci/commit/adea166cbe760c72be331b73a165251765e18530))


### Bug Fixes

* reject ambiguous explicit release asset names ([f57f5ec](https://github.com/LedFx/release-ci/commit/f57f5eced6c74aadb1d432fc349554212d7cf05f))

## Changelog

Release notes are maintained by release-please.
