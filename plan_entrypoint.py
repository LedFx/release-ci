"""Run the shared planner inside the consumer's locked wheel-build group."""

from release_ci.planner import main

if __name__ == "__main__":
    raise SystemExit(main())
