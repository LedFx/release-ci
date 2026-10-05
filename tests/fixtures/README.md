# Independent publication fixtures

The compact `*.json` inventories retain exact filenames, version, source PyPI
JSON URL and its SHA-256 from a read-only public release survey. They are
independent inputs to the example-policy comparisons, never generated from
policy expectations during a test or a publication run.

Representative METADATA, WHEEL and PKG-INFO files retain public archive header
facts (long descriptions omitted). The matching summary records the exact
archive filename/version. LedFx and aubio wheel metadata was obtained from
its public metadata sidecar; no real WHEEL file is asserted for those two.
Tests construct minimal archives around the retained metadata; these are
metadata-contract tests, not package installation tests. Compressed platform
aliases in noise and samplerate are checked against their real WHEEL headers.

Sources and observed published versions:

- `ledfx-senders` 0.2.0: 35 wheels and one sdist.
- `LedFx` 2.2.0: one wheel and one sdist; only wheel metadata inspected here.
- `aubio-ledfx` 0.4.12: 25 wheels and one sdist.
- `audio-hotplug` 0.1.0: one wheel and one sdist. Version 0.2.0 returned 404
  during this survey; its missing Trusted Publisher is a separate release
  prerequisite. Current main's Python compatibility need not match 0.1.0.
- `pyfastnoiselite-ledfx` 0.0.9: nine ABI3 wheels and one sdist.
- `samplerate-ledfx` 0.4.0: 25 wheels and one sdist. Its repository is named
  `python-samplerate-ledfx`.

No binary wheels, benchmark results, development plans or credentials are
included in these fixtures. They do not establish a live shared-action
publication or retroactive attestations for earlier releases.
