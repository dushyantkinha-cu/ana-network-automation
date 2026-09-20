#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
AUTOMATION_DIR = REPO_ROOT / "automation"
REPORT_DIR = REPO_ROOT / "validation-reports"

INVENTORY_SCRIPT = AUTOMATION_DIR / "netbox_inventory.py"
RENDER_SCRIPT = AUTOMATION_DIR / "render_config.py"
COLLECT_SCRIPT = AUTOMATION_DIR / "collect_live_configs.py"
VALIDATE_SCRIPT = AUTOMATION_DIR / "validate_intent.py"
DRIFT_SCRIPT = AUTOMATION_DIR / "report_drift.py"


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def run_command(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=os.environ,
        cwd=REPO_ROOT,
    )

    return {
        "command": [
            Path(part).name if i == 1 else part
            for i, part in enumerate(command)
        ],
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def load_inventory():
    result = subprocess.run(
        [sys.executable, str(INVENTORY_SCRIPT)],
        capture_output=True,
        text=True,
        env=os.environ,
        cwd=REPO_ROOT,
    )

    if result.returncode != 0:
        fail(
            result.stderr.strip()
            or "Unable to retrieve NetBox inventory."
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"Invalid inventory JSON: {exc}")


def print_step(name, result):
    status = "PASS" if result["returncode"] == 0 else "FAIL"

    print(f"{status}  {name}")

    if result["stdout"]:
        for line in result["stdout"].splitlines():
            print(f"      {line}")

    if result["stderr"]:
        for line in result["stderr"].splitlines():
            print(f"      {line}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete read-only configuration "
            "validation workflow."
        )
    )

    parser.add_argument(
        "--device",
        help=(
            "Validate one managed device instead of all "
            "managed devices."
        ),
    )

    parser.add_argument(
        "--skip-collect",
        action="store_true",
        help=(
            "Use existing live-configs instead of collecting "
            "fresh configurations."
        ),
    )

    parser.add_argument(
        "--details",
        action="store_true",
        help="Include detailed validation and drift output.",
    )

    args = parser.parse_args()

    inventory = load_inventory()
    devices = inventory["devices"]

    if args.device:
        devices = [
            device
            for device in devices
            if device["hostname"] == args.device
        ]

        if not devices:
            fail(
                f"Managed device {args.device!r} was not found "
                "in NetBox inventory."
            )

    report = {
        "timestamp_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "scope": (
            args.device if args.device else "all-managed-devices"
        ),
        "devices": [
            {
                "hostname": device["hostname"],
                "platform": device["platform"],
                "management_ip": device["management_ip"],
            }
            for device in devices
        ],
        "steps": {},
        "overall_status": "pass",
    }

    overall_failure = False

    print("=== RENDER INTENDED CONFIGS ===")

    render_results = []

    for device in devices:
        hostname = device["hostname"]

        result = run_command(
            [
                sys.executable,
                str(RENDER_SCRIPT),
                "--device",
                hostname,
            ]
        )

        render_results.append(
            {
                "device": hostname,
                **result,
            }
        )

        print_step(
            f"render {hostname}",
            result,
        )

        if result["returncode"] != 0:
            overall_failure = True

    report["steps"]["render"] = render_results

    if not args.skip_collect:
        print()
        print("=== COLLECT LIVE CONFIGS ===")

        collect_command = [
            sys.executable,
            str(COLLECT_SCRIPT),
        ]

        if args.device:
            collect_command.extend(
                ["--device", args.device]
            )

        collect_result = run_command(
            collect_command
        )

        print_step(
            "live collection",
            collect_result,
        )

        report["steps"]["collect"] = (
            collect_result
        )

        if collect_result["returncode"] != 0:
            overall_failure = True

    else:
        report["steps"]["collect"] = {
            "skipped": True
        }

        print()
        print(
            "SKIP  live collection "
            "(using existing live-configs)"
        )

    print()
    print("=== VALIDATE INTENDED PATHS ===")

    validate_command = [
        sys.executable,
        str(VALIDATE_SCRIPT),
    ]

    if args.device:
        validate_command.extend(
            ["--device", args.device]
        )

    if args.details:
        validate_command.append(
            "--show-missing"
        )

    validate_result = run_command(
        validate_command
    )

    print_step(
        "intent validation",
        validate_result,
    )

    report["steps"]["intent_validation"] = (
        validate_result
    )

    if validate_result["returncode"] != 0:
        overall_failure = True

    print()
    print("=== CHECK SCOPED DRIFT ===")

    drift_command = [
        sys.executable,
        str(DRIFT_SCRIPT),
    ]

    if args.device:
        drift_command.extend(
            ["--device", args.device]
        )

    if args.details:
        drift_command.append("--details")

    drift_result = run_command(
        drift_command
    )

    print_step(
        "drift validation",
        drift_result,
    )

    report["steps"]["drift"] = drift_result

    if drift_result["returncode"] != 0:
        overall_failure = True

    if overall_failure:
        report["overall_status"] = "fail"

    REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )
    REPORT_DIR.chmod(0o700)

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    scope_name = (
        args.device
        if args.device
        else "all"
    )

    report_path = (
        REPORT_DIR
        / f"validation-{scope_name}-{timestamp}.json"
    )

    report_path.write_text(
        json.dumps(
            report,
            indent=2,
        )
        + "\n"
    )

    report_path.chmod(0o600)

    print()
    print("=== VALIDATION SUMMARY ===")
    print(
        "Overall status:",
        report["overall_status"].upper(),
    )
    print(
        "Report:",
        report_path,
    )

    if overall_failure:
        sys.exit(1)


if __name__ == "__main__":
    main()
