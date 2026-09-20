#!/usr/bin/env python3

import argparse
import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = REPO_ROOT / "templates"
INVENTORY_SCRIPT = REPO_ROOT / "automation" / "netbox_inventory.py"


TEMPLATE_MAP = {
        ("Cisco IOS-XE", "edge"): "cisco/edge.j2",
        ("Arista EOS", "distribution"): "arista/distribution.j2",
        ("Arista EOS", "access"): "arista/access.j2",
        ("Arista EOS", "core"): "arista/core.j2",
        ("Nokia SR Linux", "core"): "nokia/core.j2",
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


def ios_ifname(name):
    if name == "Loopback":
        return "Loopback0"

    if name.startswith("Gi") and name[2:].isdigit():
        return f"GigabitEthernet{name[2:]}"

    return name

def arista_ifname(name):
    if name == "Loopback":
        return "Loopback0"

    if name.startswith("Et") and name[2:].isdigit():
        return f"Ethernet{name[2:]}"

    return name

def srl_ifname(name):
    if name == "Loopback":
        return "lo0"

    if name.startswith("Et-"):
        return "ethernet-" + name[3:]

    return name


def srl_subifname(name):
    return f"{srl_ifname(name)}.0"


def addresses_of_family(addresses, family):
    return [
        address
        for address in addresses
        if ipaddress.ip_interface(address).version == family
    ]

def ip_family(address):
    return ipaddress.ip_interface(address).version


def ip_address_only(address):
    return str(ipaddress.ip_interface(address).ip)


def ios_netmask(address):
    interface = ipaddress.ip_interface(address)

    if interface.version != 4:
        raise ValueError(f"{address} is not an IPv4 address")

    return str(interface.network.netmask)


def build_environment():
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_ROOT),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

    env.filters["ios_ifname"] = ios_ifname
    env.filters["ip_family"] = ip_family
    env.filters["ip_address_only"] = ip_address_only
    env.filters["ios_netmask"] = ios_netmask
    env.filters["arista_ifname"] = arista_ifname
    env.filters["srl_ifname"] = srl_ifname
    env.filters["srl_subifname"] = srl_subifname
    env.filters["addresses_of_family"] = addresses_of_family 
    
    return env


def find_device(inventory, hostname):
    for device in inventory["devices"]:
        if device["hostname"] == hostname:
            return device

    fail(f"Managed device '{hostname}' was not found in NetBox inventory.")


def main():
    parser = argparse.ArgumentParser(
        description="Render a managed device configuration from NetBox intent."
    )

    parser.add_argument(
        "--device",
        required=True,
        help="NetBox device name, for example R3",
    )

    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the rendered configuration instead of writing a file.",
    )

    args = parser.parse_args()

    inventory = load_inventory()
    device = find_device(inventory, args.device)

    key = (
        device["platform"],
        device["config_profile"],
    )

    template_name = TEMPLATE_MAP.get(key)

    if not template_name:
        fail(
            "No template is defined for "
            f"platform={device['platform']!r}, "
            f"profile={device['config_profile']!r}."
        )

    env = build_environment()
    template = env.get_template(template_name)

    rendered = template.render(
        device=device,
        ctx=device["config_context"],
    )

    if args.stdout:
        print(rendered, end="")
        return

    output_dir = REPO_ROOT / "generated-configs"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / f"{device['hostname']}.cfg"
    output_file.write_text(rendered)

    print(f"Rendered {device['hostname']} -> {output_file}")


if __name__ == "__main__":
    main()
