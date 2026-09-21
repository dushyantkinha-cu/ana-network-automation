#!/usr/bin/env python3

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from automation.device_connection import (  # noqa: E402
    get_platform_settings,
    open_connection,
)


INVENTORY_SCRIPT = (
    REPO_ROOT
    / "automation"
    / "netbox_inventory.py"
)

DEFAULT_OUTPUT_DIR = (
    REPO_ROOT
    / "live-configs"
)

COLLECTION_COMMANDS = {
    "Arista EOS": "show running-config",
    "Cisco IOS-XE": "show running-config",
    "Nokia SR Linux": "info from running /",
}

def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def load_inventory():
    result = subprocess.run(
        [sys.executable, str(INVENTORY_SCRIPT)],
        capture_output=True,
        text=True,
        env=os.environ,
    )

    if result.returncode != 0:
        fail(result.stderr.strip() or "Unable to retrieve NetBox inventory.")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"Invalid inventory JSON: {exc}")


def collect_device(device):
    platform = device["platform"]

    get_platform_settings(platform)

    command = COLLECTION_COMMANDS.get(
        platform
    )

    if not command:
        raise ValueError(
            f"Unsupported platform: "
            f"{platform!r}"
        )

    connection = open_connection(device)

    try:
        output = connection.send_command(
            command,
            read_timeout=60,
        )

        if not output.strip():
            raise RuntimeError(
                "Device returned an empty "
                "configuration."
            )

        return output.rstrip() + "\n"

    finally:
        connection.disconnect()

def write_config(output_dir, hostname, config):
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)

    path = output_dir / f"{hostname}.cfg"
    path.write_text(config)
    path.chmod(0o600)

    return path


def main():
    parser = argparse.ArgumentParser(
        description="Collect read-only live configurations from managed devices."
    )

    parser.add_argument(
        "--device",
        help="Collect only one managed device, for example R1.",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for collected live configurations.",
    )

    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.chmod(0o700)

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

    failures = 0

    for device in devices:
        hostname = device["hostname"]

        try:
            config = collect_device(device)
            path = write_config(
                args.output_dir,
                hostname,
                config,
            )

            print(
                f"PASS  {hostname:2}  "
                f"{device['platform']}  "
                f"{len(config.splitlines())} lines  "
                f"-> {path}"
            )

        except Exception as exc:
            failures += 1
            print(
                f"FAIL  {hostname:2}  "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
