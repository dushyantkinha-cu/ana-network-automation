#!/usr/bin/env python3

import argparse
import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path

from netmiko import ConnectHandler


REPO_ROOT = Path(__file__).resolve().parent.parent
INVENTORY_SCRIPT = REPO_ROOT / "automation" / "netbox_inventory.py"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "live-configs"


PLATFORM_SETTINGS = {
    "Arista EOS": {
        "device_type": "arista_eos",
        "username_env": "ARISTA_USERNAME",
        "password_env": "ARISTA_PASSWORD",
        "command": "show running-config",
        "enable": True,
    },
    "Cisco IOS-XE": {
        "device_type": "cisco_ios",
        "username_env": "CISCO_USERNAME",
        "password_env": "CISCO_PASSWORD",
        "command": "show running-config",
        "enable": False,
    },
    "Nokia SR Linux": {
        "device_type": "nokia_srl",
        "username_env": "NOKIA_USERNAME",
        "password_env": "NOKIA_PASSWORD",
        "command": "info from running /",
        "enable": False,
    },
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


def get_credentials(settings):
    username_env = settings["username_env"]
    password_env = settings["password_env"]

    username = os.environ.get(username_env)
    password = os.environ.get(password_env)

    if not username:
        fail(f"Required environment variable {username_env} is not set.")

    if not password:
        fail(f"Required environment variable {password_env} is not set.")

    return username, password


def collect_device(device):
    platform = device["platform"]
    settings = PLATFORM_SETTINGS.get(platform)

    if not settings:
        raise ValueError(f"Unsupported platform: {platform!r}")

    username, password = get_credentials(settings)
    management_ip = str(
        ipaddress.ip_interface(device["management_ip"]).ip
    )
    connection_args = {
        "device_type": settings["device_type"],
        "host": management_ip,
        "username": username,
        "password": password,
    }

    if settings["enable"]:
        connection_args["secret"] = password

    connection = ConnectHandler(**connection_args)

    try:
        if settings["enable"] and not connection.check_enable_mode():
            connection.enable()

        output = connection.send_command(
            settings["command"],
            read_timeout=60,
        )

        if not output.strip():
            raise RuntimeError("Device returned an empty configuration.")

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
