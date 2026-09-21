import json
import os
import subprocess
import sys

from webapp.clients.netbox import netbox_get
from webapp.config import (
    INVENTORY_SCRIPT,
    NETWORK_VENDORS,
    REPO_ROOT,
)


def load_inventory():
    result = subprocess.run(
        [sys.executable, str(INVENTORY_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=os.environ,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or "Unable to retrieve NetBox inventory."
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Inventory script returned invalid JSON: {exc}"
        ) from exc


def get_devices():
    inventory = load_inventory()
    return inventory.get("devices", [])


def get_sites():
    data = netbox_get(
        "/api/dcim/sites/?limit=0"
    )

    return [
        {
            "id": site["id"],
            "name": site["name"],
        }
        for site in data.get("results", [])
    ]


def get_device_types():
    data = netbox_get(
        "/api/dcim/device-types/?limit=0"
    )

    return [
        item
        for item in data.get("results", [])
        if (
            item.get("manufacturer")
            and item["manufacturer"].get("name")
            in NETWORK_VENDORS
        )
    ]


def get_platforms():
    data = netbox_get(
        "/api/dcim/platforms/?limit=0"
    )

    return [
        item
        for item in data.get("results", [])
        if (
            item.get("manufacturer")
            and item["manufacturer"].get("name")
            in NETWORK_VENDORS
        )
    ]


def get_network_roles():
    data = netbox_get(
        "/api/dcim/device-roles/?limit=0"
    )

    allowed = {
        "Router",
        "Multilayer Switch",
    }

    return [
        item
        for item in data.get("results", [])
        if item.get("name") in allowed
    ]


def get_staged_devices():
    data = netbox_get(
        "/api/dcim/devices/?status=staged"
        "&include=config_context"
        "&limit=0"
    )

    devices = []

    for item in data.get("results", []):
        custom_fields = (
            item.get("custom_fields") or {}
        )

        if (
            custom_fields.get(
                "automation_managed"
            )
            is False
        ):
            devices.append(item)

    return devices


def get_choice_values(choice_set_id):
    data = netbox_get(
        f"/api/extras/custom-field-choice-sets/"
        f"{choice_set_id}/"
    )

    choices = []

    for source in (
        data.get("base_choices") or [],
        data.get("extra_choices") or [],
    ):
        for value, label in source:
            choices.append(
                {
                    "value": value,
                    "label": label,
                }
            )

    return choices


def find_managed_device(hostname):
    devices = get_devices()

    return next(
        (
            device
            for device in devices
            if device.get("hostname") == hostname
        ),
        None,
    )
