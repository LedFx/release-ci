"""Verify same-run package artifacts and finalize the existing release draft last.

No release/tag is created or replaced. Prepare/check-upload are remote-read-only;
finalize uploads missing assets by immutable release ID, then publishes that ID.
"""

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

Hashes = dict[str, str]
Command = Callable[[list[str]], bytes]
Fetch = Callable[[str], object]


class PublicationError(RuntimeError):
    """Credential-free failure that must leave an unverified draft unpublished."""


class MissingArtifact(PublicationError):
    """A registry explicitly reported absence; never a generic network failure."""


def run_command(arguments: list[str]) -> bytes:
    try:
        result = subprocess.run(
            arguments, capture_output=True, timeout=180, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PublicationError("GitHub command failed or timed out") from None
    if result.returncode:
        if arguments[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            message = (
                result.stderr.decode("utf-8", errors="replace")
                .strip()
                .lower()
                .removeprefix("error:")
                .strip()
            )
            ref = arguments[4].lower()
            if message == f"{ref}: not found" or re.search(
                r"\bmanifest[_ ]unknown\b", message
            ):
                raise MissingArtifact("OCI artifact absent")
        # Even a missing release is fatal; only PyPI404 permits an absent file set.
        raise PublicationError("GitHub command failed")
    return result.stdout


def table(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise PublicationError("Expected an object with string keys")
    return cast(dict[str, object], value)


def array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise PublicationError("Expected an array")
    return cast(list[object], value)


def string(value: object) -> str:
    if not isinstance(value, str):
        raise PublicationError("Expected a string")
    return value


def flag(value: object) -> bool:
    if type(value) is not bool:
        raise PublicationError("Expected a boolean")
    return value


def positive_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise PublicationError("Expected a positive immutable ID")
    return value


def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PublicationError("Duplicate JSON key")
        result[key] = value
    return result


def decode(data: bytes | str) -> object:
    try:
        return json.loads(data, object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError):
        raise PublicationError("Invalid JSON metadata") from None


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()
