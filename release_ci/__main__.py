"""CLI used by the composite; permissions and identity remain in caller job."""

import argparse
import json
import os
import sys
from pathlib import Path

from .common import PublicationError
from .publisher import Publisher


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", choices=("prepare", "check-upload", "promote", "finalize")
    )
    parser.add_argument("--project", type=Path, default=Path("."))
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--docker-digests", type=Path)
    parser.add_argument(
        "--wheel-plan",
        help="Pre-build JSON plan required for cibuildwheel; omit for pure",
    )
    args = parser.parse_args()
    try:
        tag = os.environ.get("GITHUB_REF_NAME", "")
        if (
            os.environ.get("GITHUB_EVENT_NAME") != "push"
            or os.environ.get("GITHUB_REF") != "refs/tags/" + tag
            or not os.environ.get("GH_TOKEN")
        ):
            raise PublicationError(
                "Only a canonical tag-push job with a scoped token can publish"
            )
        workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
        temporary = Path(os.environ["RUNNER_TEMP"]).resolve()
        for path in (
            args.project,
            args.project / "pyproject.toml",
            args.dist,
            args.assets,
            args.docker_digests,
        ):
            if path is not None and not path.resolve().is_relative_to(workspace):
                raise PublicationError("Input path must be inside caller workspace")
        if (
            not args.snapshot.resolve().is_relative_to(temporary)
            or args.snapshot.is_symlink()
        ):
            raise PublicationError("Snapshot must be an owned runner temporary file")
        publisher = Publisher(
            args.dist,
            args.snapshot,
            os.environ.get("GITHUB_REPOSITORY", ""),
            tag,
            os.environ.get("GITHUB_SHA", ""),
            project=args.project,
            workflow_ref=os.environ.get("GITHUB_WORKFLOW_REF", ""),
            assets=args.assets,
            docker_digests=args.docker_digests,
            wheel_plan=args.wheel_plan,
        )
        if args.phase == "promote":
            outputs: dict[str, str | bool] = {
                "image_digests": json.dumps(publisher.promote(), sort_keys=True)
            }
        else:
            outputs = dict(
                {
                    "prepare": publisher.prepare,
                    "check-upload": publisher.check_upload,
                    "finalize": publisher.finalize,
                }[args.phase]()
            )
        destination = os.environ.get("GITHUB_OUTPUT")
        if destination:
            with Path(destination).open("a") as handle:
                for key, value in outputs.items():
                    text = str(value).lower() if isinstance(value, bool) else value
                    handle.write(f"{key}={text}\n")
        print(json.dumps(outputs, sort_keys=True))
    except PublicationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError):
        print("Invalid or unavailable release inputs", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
