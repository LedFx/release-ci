"""Execute the SHA-pinned action's package without installing consumer code."""

from release_ci.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
