#!/usr/bin/env python3

import argparse
import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INVENTORY_SCRIPT = REPO_ROOT / "automation" / "netbox_inventory.py"

GENERATED_DIR = REPO_ROOT / "generated-configs"
LIVE_DIR = REPO_ROOT / "live-configs"


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


def normalize_ipv6_token(token):
    if ":" not in token:
        return token

    try:
        if "/" in token:
            return str(ipaddress.IPv6Interface(token))
        return str(ipaddress.IPv6Address(token))
    except ValueError:
        return token


def normalize_command(command):
    words = command.strip().split()

    return " ".join(
        normalize_ipv6_token(word)
        for word in words
    )


def parse_indented_config(text):
    """
    Convert IOS/EOS-style indented configuration into canonical command paths.

    Example:

        interface Ethernet1
           no switchport
           ip address 10.0.0.1/31

    becomes:

        interface Ethernet1
        interface Ethernet1 / no switchport
        interface Ethernet1 / ip address 10.0.0.1/31
    """

    paths = set()
    stack = []

    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue

        stripped = raw_line.strip()

        if stripped == "!" or stripped.startswith("!"):
            continue

        if stripped == "end":
            continue

        # Ignore common Cisco command-output headers.
        if stripped.startswith("Building configuration"):
            continue

        if stripped.startswith("Current configuration"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())
        command = normalize_command(stripped)

        while stack and stack[-1][0] >= indent:
            stack.pop()

        path = tuple(
            item[1] for item in stack
        ) + (command,)

        paths.add(path)
        stack.append((indent, command))

    return paths


def parse_srlinux_config(text):
    """
    Convert SR Linux brace-based configuration into canonical paths.
    """

    paths = set()
    stack = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()

        if not stripped:
            continue

        if stripped.startswith("--{"):
            continue

        if stripped.startswith("A:") and stripped.endswith("#"):
            continue

        if stripped == "}":
            if stack:
                stack.pop()
            continue

        if stripped.endswith("{"):
            command = normalize_command(
                stripped[:-1].strip()
            )

            path = tuple(stack) + (command,)
            paths.add(path)
            stack.append(command)
            continue

        command = normalize_command(stripped)
        path = tuple(stack) + (command,)
        paths.add(path)

    return paths


def parse_config(platform, text):
    if platform in {"Arista EOS", "Cisco IOS-XE"}:
        return parse_indented_config(text)

    if platform == "Nokia SR Linux":
        return parse_srlinux_config(text)

    raise ValueError(
        f"Unsupported platform: {platform!r}"
    )


def format_path(path):
    return " / ".join(path)


def validate_device(device):
    hostname = device["hostname"]
    platform = device["platform"]

    generated_path = GENERATED_DIR / f"{hostname}.cfg"
    live_path = LIVE_DIR / f"{hostname}.cfg"

    if not generated_path.is_file():
        raise FileNotFoundError(
            f"Generated configuration missing: {generated_path}"
        )

    if not live_path.is_file():
        raise FileNotFoundError(
            f"Live configuration missing: {live_path}"
        )

    generated_text = generated_path.read_text()
    live_text = live_path.read_text()

    intended_paths = parse_config(
        platform,
        generated_text,
    )

    live_paths = parse_config(
        platform,
        live_text,
    )

    missing = sorted(
        intended_paths - live_paths,
        key=format_path,
    )

    return {
        "hostname": hostname,
        "platform": platform,
        "intended_count": len(intended_paths),
        "live_count": len(live_paths),
        "missing": missing,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Validate that generated managed intent exists "
            "in live device configurations."
        )
    )

    parser.add_argument(
        "--device",
        help="Validate one managed device, for example R1.",
    )

    parser.add_argument(
        "--show-missing",
        action="store_true",
        help="Display missing intended configuration paths.",
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

    failures = 0

    for device in devices:
        try:
            result = validate_device(device)

            if result["missing"]:
                failures += 1

                print(
                    f"FAIL  {result['hostname']:2}  "
                    f"{result['platform']}  "
                    f"{len(result['missing'])} intended paths missing"
                )

                if args.show_missing:
                    for path in result["missing"]:
                        print(f"      - {format_path(path)}")

            else:
                print(
                    f"PASS  {result['hostname']:2}  "
                    f"{result['platform']}  "
                    f"{result['intended_count']} intended paths verified"
                )

        except Exception as exc:
            failures += 1
            print(
                f"FAIL  {device['hostname']:2}  "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
