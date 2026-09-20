#!/usr/bin/env python3

import argparse
import difflib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "golden-configs"


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def snapshot_dirs():
    return sorted(
        (
            path
            for path in GOLDEN_DIR.iterdir()
            if path.is_dir()
            and (path / "manifest.json").is_file()
        ),
        key=lambda path: path.name,
    )


def resolve_snapshot(value):
    path = Path(value)

    if not path.is_absolute():
        candidate = GOLDEN_DIR / value

        if candidate.is_dir():
            path = candidate
        else:
            path = (
                REPO_ROOT / value
            )

    path = path.resolve()

    if not path.is_dir():
        fail(
            f"Golden snapshot does not exist: {path}"
        )

    if not (path / "manifest.json").is_file():
        fail(
            f"Snapshot has no manifest.json: {path}"
        )

    return path


def load_manifest(snapshot):
    path = snapshot / "manifest.json"

    try:
        with path.open() as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        fail(
            f"Unable to read {path}: {exc}"
        )


def device_map(manifest):
    devices = {}

    for device in manifest.get("devices", []):
        hostname = device.get("hostname")

        if not hostname:
            fail(
                "Manifest contains a device "
                "without a hostname."
            )

        devices[hostname] = device

    return devices


def read_config(snapshot, record):
    filename = record.get("snapshot_file")

    if not filename:
        fail(
            f"Device {record.get('hostname')} has "
            "no snapshot_file in manifest."
        )

    path = snapshot / filename

    if not path.is_file():
        fail(
            f"Golden config missing: {path}"
        )

    return path.read_text().splitlines()


def show_diff(
    hostname,
    old_snapshot,
    old_record,
    new_snapshot,
    new_record,
):
    old_lines = read_config(
        old_snapshot,
        old_record,
    )

    new_lines = read_config(
        new_snapshot,
        new_record,
    )

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=(
            f"{old_snapshot.name}/{hostname}.cfg"
        ),
        tofile=(
            f"{new_snapshot.name}/{hostname}.cfg"
        ),
        lineterm="",
    )

    for line in diff:
        print(f"      {line}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Compare two timestamped golden "
            "configuration snapshots."
        )
    )

    parser.add_argument(
        "--from-snapshot",
        help=(
            "Older snapshot ID or path. "
            "Defaults to the second-newest snapshot."
        ),
    )

    parser.add_argument(
        "--to-snapshot",
        help=(
            "Newer snapshot ID or path. "
            "Defaults to the newest snapshot."
        ),
    )

    parser.add_argument(
        "--details",
        action="store_true",
        help=(
            "Show unified configuration diffs "
            "for changed devices."
        ),
    )

    args = parser.parse_args()

    snapshots = snapshot_dirs()

    if args.from_snapshot:
        old_snapshot = resolve_snapshot(
            args.from_snapshot
        )
    else:
        if len(snapshots) < 2:
            fail(
                "At least two golden snapshots are "
                "required when --from-snapshot is omitted."
            )

        old_snapshot = snapshots[-2]

    if args.to_snapshot:
        new_snapshot = resolve_snapshot(
            args.to_snapshot
        )
    else:
        if not snapshots:
            fail(
                "No golden snapshots were found."
            )

        new_snapshot = snapshots[-1]

    old_manifest = load_manifest(
        old_snapshot
    )

    new_manifest = load_manifest(
        new_snapshot
    )

    old_devices = device_map(
        old_manifest
    )

    new_devices = device_map(
        new_manifest
    )

    hostnames = sorted(
        set(old_devices)
        | set(new_devices)
    )

    changed = 0
    unchanged = 0
    added = 0
    removed = 0

    print(
        "From:",
        old_snapshot.name,
    )

    print(
        "To:",
        new_snapshot.name,
    )

    print()

    for hostname in hostnames:
        old_record = old_devices.get(
            hostname
        )

        new_record = new_devices.get(
            hostname
        )

        if old_record is None:
            added += 1

            print(
                f"ADDED      {hostname}"
            )

            continue

        if new_record is None:
            removed += 1

            print(
                f"REMOVED    {hostname}"
            )

            continue

        if (
            old_record.get("sha256")
            == new_record.get("sha256")
        ):
            unchanged += 1

            print(
                f"UNCHANGED  {hostname}"
            )

            continue

        changed += 1

        print(
            f"CHANGED    {hostname}"
        )

        print(
            "      old:",
            old_record.get("sha256"),
        )

        print(
            "      new:",
            new_record.get("sha256"),
        )

        if args.details:
            show_diff(
                hostname,
                old_snapshot,
                old_record,
                new_snapshot,
                new_record,
            )

    print()
    print("=== HISTORY SUMMARY ===")
    print("Changed:", changed)
    print("Unchanged:", unchanged)
    print("Added:", added)
    print("Removed:", removed)


if __name__ == "__main__":
    main()
