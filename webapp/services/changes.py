import ipaddress
import re

from webapp.clients.netbox import netbox_get


def make_slug(value):
    slug = value.strip().lower()

    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        slug,
    )

    return slug.strip("-")


def get_wan_address_state(device):
    device_id = device.get("device_id")

    if not device_id:
        raise RuntimeError(
            "Managed device has no NetBox device ID."
        )

    context = device.get("config_context") or {}
    nat = context.get("nat") or {}

    outside_interface = nat.get(
        "outside_interface"
    )

    if not outside_interface:
        return None

    interfaces = netbox_get(
        "/api/dcim/interfaces/"
        f"?device_id={device_id}"
        f"&name={outside_interface}"
    ).get("results", [])

    if len(interfaces) != 1:
        raise RuntimeError(
            f"Unable to uniquely resolve "
            f"{device['hostname']} "
            f"WAN interface {outside_interface}."
        )

    interface = interfaces[0]

    ip_objects = netbox_get(
        "/api/ipam/ip-addresses/"
        f"?interface_id={interface['id']}"
        "&limit=0"
    ).get("results", [])

    ipv4 = None
    ipv6 = None

    for ip_object in ip_objects:
        address = ip_object.get("address")

        if not address:
            continue

        parsed = ipaddress.ip_interface(
            address
        )

        record = {
            "id": ip_object["id"],
            "address": address,
            "prefixlen": parsed.network.prefixlen,
        }

        if parsed.version == 4:
            if ipv4 is not None:
                raise RuntimeError(
                    "WAN interface has multiple IPv4 "
                    "addresses; automatic editing is "
                    "not safe."
                )

            ipv4 = record

        else:
            if ipv6 is not None:
                raise RuntimeError(
                    "WAN interface has multiple IPv6 "
                    "addresses; automatic editing is "
                    "not safe."
                )

            ipv6 = record

    return {
        "interface_id": interface["id"],
        "interface_name": outside_interface,
        "ipv4": ipv4,
        "ipv6": ipv6,
    }
