#!/usr/bin/env python3

import argparse
import os
import sys
from pathlib import Path

from validate_intent import (
    GENERATED_DIR,
    LIVE_DIR,
    format_path,
    load_inventory,
    parse_config,
)


SENSITIVE_TERMS = {
    "password",
    "secret",
    "community",
    "authentication-key",
    "privacy-key",
    "private-key",
}


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def contains_sensitive_data(path):
    text = " ".join(path).lower()

    return any(
        term in text
        for term in SENSITIVE_TERMS
    )


def intended_roots(intended_paths):
    return {
        path[0]
        for path in intended_paths
        if path
    }


def srl_owned_path(path, intended_paths):
    if not path:
        return False

    roots = intended_roots(intended_paths)

    # Entire explicitly generated interface blocks are owned.
    if path[0].startswith("interface "):
        return path[0] in roots

    if path[0] != "network-instance default":
        return False

    # Direct network-instance interface attachments.
    if len(path) >= 2 and path[1].startswith("interface "):
        intended_ni_interfaces = {
            p[1]
            for p in intended_paths
            if len(p) >= 2
            and p[0] == "network-instance default"
            and p[1].startswith("interface ")
        }

        return path[1] in intended_ni_interfaces

    # Only the OSPF hierarchy is owned beneath protocols.
    if (
        len(path) >= 4
        and path[1] == "protocols"
        and path[2] == "ospf"
        and path[3].startswith("instance ")
    ):
        intended_instances = {
            p[3]
            for p in intended_paths
            if len(p) >= 4
            and p[0] == "network-instance default"
            and p[1] == "protocols"
            and p[2] == "ospf"
            and p[3].startswith("instance ")
        }

        return path[3] in intended_instances

    # Keep structural parents needed for the managed OSPF tree.
    return path in {
        ("network-instance default",),
        ("network-instance default", "protocols"),
        ("network-instance default", "protocols", "ospf"),
    }


def owned_live_path(platform, path, intended_paths):
    if platform == "Nokia SR Linux":
        return srl_owned_path(
            path,
            intended_paths,
        )

    if platform in {
        "Arista EOS",
        "Cisco IOS-XE",
    }:
        roots = intended_roots(intended_paths)

        return bool(path) and path[0] in roots

    raise ValueError(
        f"Unsupported platform: {platform!r}"
    )


def compare_device(device):
    hostname = device["hostname"]
    platform = device["platform"]

    generated_path = (
        GENERATED_DIR / f"{hostname}.cfg"
    )

    live_path = (
        LIVE_DIR / f"{hostname}.cfg"
    )

    if not generated_path.is_file():
        raise FileNotFoundError(
            f"Generated configuration missing: "
            f"{generated_path}"
        )

    if not live_path.is_file():
        raise FileNotFoundError(
            f"Live configuration missing: "
            f"{live_path}"
        )

    intended_paths = parse_config(
        platform,
        generated_path.read_text(),
    )

    live_paths = parse_config(
        platform,
        live_path.read_text(),
    )

    owned_live_paths = {
        path
        for path in live_paths
        if owned_live_path(
            platform,
            path,
            intended_paths,
        )
    }

    missing = sorted(
        intended_paths - live_paths,
        key=format_path,
    )

    extra = sorted(
        (
            path
            for path in owned_live_paths - intended_paths
            if not contains_sensitive_data(path)
        ),
        key=format_path,
    )

    return {
        "hostname": hostname,
        "platform": platform,
        "missing": missing,
        "extra": extra,
    }


def print_paths(title, paths):
    if not paths:
        return

    print(f"      {title}:")

    for path in paths:
        print(
            f"        - {format_path(path)}"
        )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Report scoped configuration drift "
            "within template-managed configuration areas."
        )
    )

    parser.add_argument(
        "--device",
        help=(
            "Report drift for one managed device, "
            "for example R1."
        ),
    )

    parser.add_argument(
        "--details",
        action="store_true",
        help=(
            "Display individual missing and "
            "extra configuration paths."
        ),
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
                f"Managed device {args.device!r} "
                "was not found in NetBox inventory."
            )

    drifted = 0

    for device in devices:
        try:
            result = compare_device(device)

            missing_count = len(
                result["missing"]
            )

            extra_count = len(
                result["extra"]
            )

            if not missing_count and not extra_count:
                print(
                    f"PASS   {result['hostname']:2}  "
                    f"{result['platform']}  "
                    "no scoped drift"
                )
                continue

            drifted += 1

            print(
                f"DRIFT  {result['hostname']:2}  "
                f"{result['platform']}  "
                f"missing={missing_count}  "
                f"extra={extra_count}"
            )

            if args.details:
                print_paths(
                    "Missing intended paths",
                    result["missing"],
                )

                print_paths(
                    "Extra live paths",
                    result["extra"],
                )

        except Exception as exc:
            drifted += 1

            print(
                f"FAIL   {device['hostname']:2}  "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    if drifted:
        sys.exit(1)


if __name__ == "__main__":
    main()
