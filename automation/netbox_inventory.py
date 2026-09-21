#!/usr/bin/env python3

import json
import os
import sys
from collections import defaultdict

import requests


NETBOX_URL = os.environ.get("NETBOX_URL", "").rstrip("/")
NETBOX_TOKEN = os.environ.get("NETBOX_TOKEN", "")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


if not NETBOX_URL:
    fail("NETBOX_URL is not set.")

if not NETBOX_TOKEN:
    fail("NETBOX_TOKEN is not set.")


session = requests.Session()
session.headers.update(
    {
        "Authorization": f"Token {NETBOX_TOKEN}",
        "Accept": "application/json",
    }
)


def get_all(endpoint):
    """
    Retrieve all objects from a paginated NetBox API endpoint.
    """

    url = f"{NETBOX_URL}/api/{endpoint.lstrip('/')}"
    params = {"limit": 1000}
    results = []

    while url:
        response = session.get(
            url,
            params=params,
            timeout=15,
        )
        response.raise_for_status()

        data = response.json()

        if not isinstance(data, dict) or "results" not in data:
            return data

        results.extend(data["results"])

        url = data.get("next")
        params = None

    return results


try:
    devices = get_all("dcim/devices/?include=config_context")
    interfaces = get_all("dcim/interfaces/")
    ip_addresses = get_all("ipam/ip-addresses/")
    vlans = get_all("ipam/vlans/")
    fhrp_groups = get_all("ipam/fhrp-groups/")
    fhrp_assignments = get_all("ipam/fhrp-group-assignments/")
except requests.RequestException as exc:
    fail(f"NetBox API request failed: {exc}")


interfaces_by_device = defaultdict(list)
ips_by_interface = defaultdict(list)
fhrp_vips_by_group = defaultdict(list)
fhrp_by_interface = defaultdict(list)
vlans_by_site = defaultdict(list)


for ip in ip_addresses:
    if ip.get("assigned_object_type") != "dcim.interface":
        continue

    interface_id = ip.get("assigned_object_id")

    if interface_id is not None:
        ips_by_interface[interface_id].append(ip["address"])

for group in fhrp_groups:
    for ip in group.get("ip_addresses") or []:
        fhrp_vips_by_group[group["id"]].append(ip["address"])

for assignment in fhrp_assignments:
    if assignment.get("interface_type") != "dcim.interface":
        continue

    interface_id = assignment.get("interface_id")
    group = assignment.get("group") or {}
    group_object_id = group.get("id")

    if interface_id is None or group_object_id is None:
        continue

    fhrp_by_interface[interface_id].append(
        {
            "protocol": group.get("protocol"),
            "group_id": group.get("group_id"),
            "priority": assignment.get("priority"),
            "vip_addresses": sorted(
                fhrp_vips_by_group.get(group_object_id, [])
            ),
        }
    )


for interface in interfaces:
    device = interface.get("device") or {}
    device_id = device.get("id")

    if device_id is None:
        continue

    mode = interface.get("mode")

    if isinstance(mode, dict):
        mode = mode.get("value")

    untagged_vlan = interface.get("untagged_vlan")

    if isinstance(untagged_vlan, dict):
        untagged_vlan = untagged_vlan.get("vid")

    tagged_vlans = [
        vlan["vid"]
        for vlan in interface.get("tagged_vlans", [])
        if isinstance(vlan, dict)
    ]

    interface_record = {
        "name": interface["name"],
        "type": (
            interface["type"].get("value")
            if isinstance(interface.get("type"), dict)
            else interface.get("type")
        ),
        "enabled": interface.get("enabled"),
        "mgmt_only": interface.get("mgmt_only"),
        "mode": mode,
        "untagged_vlan": untagged_vlan,
        "tagged_vlans": sorted(tagged_vlans),
        "ip_addresses": sorted(
            ips_by_interface.get(interface["id"], [])
        ),
        "fhrp_groups": sorted(
            fhrp_by_interface.get(interface["id"], []),
            key=lambda item: (
                item["group_id"],
                item["protocol"] or "",
            ),
        ),
    }

    interfaces_by_device[device_id].append(interface_record)


for vlan in vlans:
    site = vlan.get("site")
    status = vlan.get("status") or {}

    if not site:
        continue

    status_value = (
        status.get("value")
        if isinstance(status, dict)
        else status
    )

    if status_value != "active":
        continue

    vlans_by_site[site["id"]].append(
        {
            "vid": vlan["vid"],
            "name": vlan["name"],
        }
    )


inventory = []


for device in devices:
    custom_fields = device.get("custom_fields") or {}

    # Hard automation safety gate.
    if custom_fields.get("automation_managed") is not True:
        continue

    platform = device.get("platform") or {}
    role = device.get("role") or {}
    device_type = device.get("device_type") or {}
    site = device.get("site") or {}
    status = device.get("status") or {}

    status_value = (
        status.get("value")
        if isinstance(status, dict)
        else status
    )

    manufacturer = None

    platform_manufacturer = platform.get("manufacturer")
    if isinstance(platform_manufacturer, dict):
        manufacturer = platform_manufacturer.get("name")

    if not manufacturer:
        type_manufacturer = device_type.get("manufacturer")
        if isinstance(type_manufacturer, dict):
            manufacturer = type_manufacturer.get("name")

    primary_ip4 = device.get("primary_ip4")

    management_ip = None

    if isinstance(primary_ip4, dict):
        management_ip = primary_ip4.get("address")

    device_interfaces = sorted(
        interfaces_by_device.get(device["id"], []),
        key=lambda item: item["name"],
    )

    device_vlans = sorted(
        vlans_by_site.get(site.get("id"), []),
        key=lambda item: item["vid"],
    )

    inventory.append(
        {
            "hostname": device["name"],
            "device_id": device["id"],
            "status": status_value,
            "manufacturer": manufacturer,
            "platform": platform.get("name"),
            "role": role.get("name"),
            "management_ip": management_ip,
            "config_profile": (
                custom_fields.get("config_profile", {}).get("value")
                if isinstance(custom_fields.get("config_profile"), dict)
                else custom_fields.get("config_profile")
            ),
            "routing_protocols": sorted(
                item.get("value") if isinstance(item, dict) else item
                for item in (custom_fields.get("routing_protocols") or [])
            ),
            "config_context": device.get("config_context") or {},
            "automation_managed": True,
            "interfaces": device_interfaces,
            "vlans": device_vlans,
        }
    )


inventory.sort(key=lambda device: device["hostname"])


print(
    json.dumps(
        {
            "managed_device_count": len(inventory),
            "devices": inventory,
        },
        indent=2,
    )
)
