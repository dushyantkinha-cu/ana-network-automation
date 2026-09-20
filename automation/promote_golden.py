#!/usr/bin/env python3

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED_DIR = REPO_ROOT / "generated-configs"
REPORT_DIR = REPO_ROOT / "validation-reports"
GOLDEN_DIR = REPO_ROOT / "golden-configs"


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def git_output(*args):
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        fail(result.stderr.strip() or "Git command failed.")

    return result.stdout.strip()


def latest_validation_report():
    reports = sorted(
        REPORT_DIR.glob("validation-*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not reports:
        fail("No validation reports were found.")

    return reports[0]


def load_report(path):
    try:
        with path.open() as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Unable to read validation report: {exc}")


def sha256_file(path):
    digest = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def report_snapshot_id(report_path):
    stem = report_path.stem

    marker = "validation-all-"

    if not stem.startswith(marker):
        fail(
            "Golden promotion requires an "
            "all-managed-devices validation report."
        )

    snapshot_id = stem[len(marker):]

    if not snapshot_id:
        fail("Unable to derive snapshot timestamp from report name.")

    return snapshot_id


def validate_report(report, report_path):
    if report.get("overall_status") != "pass":
        fail("Validation report overall_status is not 'pass'.")

    if report.get("scope") != "all-managed-devices":
        fail(
            "Golden promotion requires scope "
            "'all-managed-devices'."
        )

    devices = report.get("devices") or []

    if not devices:
        fail("Validation report contains no managed devices.")

    hostnames = [
        device.get("hostname")
        for device in devices
    ]

    if any(not hostname for hostname in hostnames):
        fail(
            "Validation report contains a device "
            "without a hostname."
        )

    if len(hostnames) != len(set(hostnames)):
        fail(
            "Validation report contains duplicate "
            "device hostnames."
        )

    expected = {
        "R1", "R2", "R3", "R4",
        "S1", "S2", "S3", "S4",
    }

    if set(hostnames) != expected:
        fail(
            "Validation report device set does not match "
            "the eight organization-managed devices."
        )

    return devices


def validate_generated_configs(devices):
    records = []

    for device in devices:
        hostname = device["hostname"]

        path = (
            GENERATED_DIR
            / f"{hostname}.cfg"
        )

        if not path.is_file():
            fail(
                f"Generated configuration missing: {path}"
            )

        if path.stat().st_size == 0:
            fail(
                f"Generated configuration is empty: {path}"
            )

        records.append(
            {
                "hostname": hostname,
                "platform": device.get("platform"),
                "management_ip": device.get("management_ip"),
                "source_path": (
                    f"generated-configs/{hostname}.cfg"
                ),
                "snapshot_file": f"{hostname}.cfg",
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "_source": path,
            }
        )

    return records


def ensure_clean_git():
    status = git_output("status", "--porcelain")

    if status:
        fail(
            "Git working tree is not clean. "
            "Commit or discard changes before promoting "
            "a golden snapshot."
        )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Promote validated generated configurations "
            "to a timestamped golden snapshot."
        )
    )

    parser.add_argument(
        "--report",
        type=Path,
        help=(
            "Validation report to use. "
            "Defaults to the newest validation report."
        ),
    )

    parser.add_argument(
        "--check-only",
        action="store_true",
        help=(
            "Validate the promotion candidate without "
            "creating a golden snapshot."
        ),
    )

    args = parser.parse_args()

    report_path = (
        args.report.resolve()
        if args.report
        else latest_validation_report()
    )

    if not report_path.is_file():
        fail(
            f"Validation report does not exist: "
            f"{report_path}"
        )

    report = load_report(report_path)
    devices = validate_report(
        report,
        report_path,
    )

    config_records = validate_generated_configs(
        devices
    )

    snapshot_id = report_snapshot_id(
        report_path
    )

    snapshot_dir = (
        GOLDEN_DIR / snapshot_id
    )

    print("Validation report:", report_path)
    print("Snapshot ID:", snapshot_id)
    print(
        "Devices:",
        ", ".join(
            record["hostname"]
            for record in config_records
        ),
    )

    print()
    print("Configuration hashes:")

    for record in config_records:
        print(
            f"  {record['hostname']:2}  "
            f"{record['sha256']}"
        )

    if args.check_only:
        print()
        print(
            "CHECK PASS: candidate is eligible "
            "for golden promotion."
        )
        return

    ensure_clean_git()

    if snapshot_dir.exists():
        fail(
            f"Golden snapshot already exists: "
            f"{snapshot_dir}"
        )

    snapshot_dir.mkdir(
        parents=True,
        mode=0o755,
    )

    for record in config_records:
        destination = (
            snapshot_dir
            / record["snapshot_file"]
        )

        shutil.copy2(
            record["_source"],
            destination,
        )

    validation_copy = (
        snapshot_dir
        / "validation-report.json"
    )

    shutil.copy2(
        report_path,
        validation_copy,
    )

    manifest_devices = []

    for record in config_records:
        clean_record = {
            key: value
            for key, value in record.items()
            if key != "_source"
        }

        manifest_devices.append(
            clean_record
        )

    manifest = {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "created_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "validation": {
            "source_report": report_path.name,
            "snapshot_report": "validation-report.json",
            "report_sha256": sha256_file(
                report_path
            ),
            "timestamp_utc": report.get(
                "timestamp_utc"
            ),
            "scope": report.get("scope"),
            "overall_status": report.get(
                "overall_status"
            ),
        },
        "git": {
            "commit": git_output(
                "rev-parse",
                "HEAD",
            ),
            "branch": git_output(
                "branch",
                "--show-current",
            ),
        },
        "devices": manifest_devices,
    }

    manifest_path = (
        snapshot_dir / "manifest.json"
    )

    manifest_path.write_text(
        json.dumps(
            manifest,
            indent=2,
        )
        + "\n"
    )

    print()
    print(
        "Golden snapshot created:",
        snapshot_dir,
    )
    print(
        "Manifest:",
        manifest_path,
    )


if __name__ == "__main__":
    main()
