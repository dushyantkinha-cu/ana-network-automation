"""Isolated Lab 9 ZTP bootstrap authorization and rendering tests."""

import re
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import webapp.routers.onboarding as ob


ROUTER_CONFIG = """hostname R8
!
ip routing
!
interface Ethernet1
   no switchport
   ip address 10.1.0.15/31
!
"""

SWITCH_CONFIG = """hostname S5
!
vlan 10
!
interface Ethernet1
   switchport mode access
   switchport access vlan 10
!
interface Ethernet2
   switchport mode access
   switchport access vlan 10
!
interface Ethernet3
   switchport mode access
   switchport access vlan 10
!
"""


def device(name, device_id, serial, mac, variant, *,
           status="active", managed=True, profile=None):
    if profile is None:
        profile = (
            "access" if variant == "ztp_switch"
            else "distribution"
        )

    context = {
        "provisioning_variant": variant,
        "ztp_system_mac": mac,
    }
    if variant == "ztp_switch":
        context["vlan_access_ports"] = {
            "Et1": 10, "Et2": 10, "Et3": 10,
        }

    return {
        "id": device_id,
        "name": name,
        "serial": serial,
        "status": {"value": status},
        "platform": {"name": "Arista EOS"},
        "custom_fields": {
            "config_profile": {"value": profile},
            "automation_managed": managed,
        },
        "local_context_data": context,
    }


def request(mac, serial):
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/ztp/bootstrap",
        "headers": [
            (b"x-arista-systemmac", mac.encode()),
            (b"x-arista-serial", serial.encode()),
        ],
    })


@pytest.fixture
def ztp(monkeypatch):
    state = {
        "devices": {},
        "renders": {
            "R8": ROUTER_CONFIG,
            "S5": SWITCH_CONFIG,
        },
        "calls": [],
        "lookups": [],
        "returncode": 0,
    }

    def netbox_get(path):
        state["lookups"].append(path)
        if path.startswith("/api/dcim/devices/?"):
            query = parse_qs(urlsplit(path).query)
            serial = query.get("serial", [""])[0]
            matches = [
                {"id": d["id"], "serial": d["serial"]}
                for d in state["devices"].values()
                if d["serial"] == serial
            ]
            return {"results": matches, "next": None}

        match = re.fullmatch(
            r"/api/dcim/devices/([0-9]+)/", path
        )
        if match:
            return state["devices"][int(match.group(1))]
        raise AssertionError("Unexpected NetBox API lookup")

    def fake_run(command, **kwargs):
        name = command[command.index("--device") + 1]
        state["calls"].append(name)
        return SimpleNamespace(
            stdout=state["renders"].get(name, ""),
            returncode=state["returncode"],
        )

    monkeypatch.setattr(ob, "netbox_get", netbox_get)
    monkeypatch.setattr(subprocess, "run", fake_run)

    state["devices"][17] = device(
        "R8", 17, "TEST-R8-SERIAL",
        "001c.733c.9337", "ztp_router",
    )
    state["devices"][18] = device(
        "S5", 18, "TEST-S5-SERIAL",
        "001c.739f.275d", "ztp_switch",
    )
    return state


def assert_rejected(mac, serial, status):
    with pytest.raises(HTTPException) as raised:
        ob.ztp_bootstrap(request(mac, serial))
    assert raised.value.status_code == status


@pytest.mark.parametrize(
    "name,mac,serial",
    [
        ("R8", "001c.733c.9337", "TEST-R8-SERIAL"),
        ("S5", "001c.739f.275d", "TEST-S5-SERIAL"),
    ],
)
def test_authorized_router_and_switch(ztp, name, mac, serial):
    response = ob.ztp_bootstrap(request(mac, serial))
    assert response.status_code == 200
    assert response.body.decode() == ztp["renders"][name]
    assert ztp["calls"] == [name]


@pytest.mark.parametrize("name,device_id", [("R6", 15), ("R7", 16)])
def test_legacy_router_identity_compatibility(
    ztp, monkeypatch, name, device_id
):
    mac = "001c731e1576" if name == "R6" else "001c7364086f"
    serial = f"TEST-{name}-LEGACY"
    monkeypatch.setattr(
        ob,
        "_ztp_legacy_identities",
        lambda: {(mac, serial): (name, device_id)},
    )
    ztp["devices"][device_id] = device(
        name, device_id, serial, mac, "ztp_router"
    )
    ztp["devices"][device_id]["local_context_data"].pop(
        "ztp_system_mac"
    )
    ztp["renders"][name] = f"hostname {name}\n!\nip routing\n"

    response = ob.ztp_bootstrap(request(mac, serial))

    assert response.status_code == 200
    assert ztp["calls"] == [name]
    assert not any("?serial=" in url for url in ztp["lookups"])


@pytest.mark.parametrize(
    "status,managed",
    [
        ("staged", False),
        ("staged", True),
        ("active", False),
    ],
)
def test_staged_or_unmanaged_devices_denied(
    ztp, status, managed
):
    record = ztp["devices"][17]
    record["status"] = {"value": status}
    record["custom_fields"]["automation_managed"] = managed

    assert_rejected("001c.733c.9337", "TEST-R8-SERIAL", 403)
    assert ztp["calls"] == []


def test_wrong_mac_denied_without_rendering(ztp):
    assert_rejected("001c.733c.9999", "TEST-R8-SERIAL", 403)
    assert ztp["calls"] == []


def test_wrong_serial_denied_without_rendering(ztp):
    assert_rejected("001c.733c.9337", "WRONG-SERIAL", 403)
    assert ztp["calls"] == []


def test_malformed_mac_denied_without_rendering(ztp):
    assert_rejected("not-a-mac", "TEST-R8-SERIAL", 403)
    assert ztp["calls"] == []


def test_unapproved_profile_denied(ztp):
    ztp["devices"][18]["custom_fields"]["config_profile"] = {
        "value": "distribution"
    }
    assert_rejected("001c.739f.275d", "TEST-S5-SERIAL", 403)
    assert ztp["calls"] == []


def test_invalid_router_render_denied(ztp):
    ztp["renders"]["R8"] = "hostname R8\n!\n"
    assert_rejected("001c.733c.9337", "TEST-R8-SERIAL", 503)


def test_invalid_switch_render_denied(ztp):
    ztp["renders"]["S5"] = "hostname S5\n!\nip routing\n"
    assert_rejected("001c.739f.275d", "TEST-S5-SERIAL", 503)


def test_switch_missing_access_port_denied(ztp):
    ztp["renders"]["S5"] = SWITCH_CONFIG.replace(
        "   switchport access vlan 10\n", "", 1
    )
    assert_rejected("001c.739f.275d", "TEST-S5-SERIAL", 503)


def test_renderer_nonzero_exit_denied(ztp):
    ztp["returncode"] = 1
    assert_rejected("001c.733c.9337", "TEST-R8-SERIAL", 503)


def test_legacy_devices_cannot_bypass_identity_mapping(ztp):
    ztp["devices"][15] = device(
        "R6", 15, "TEST-R6-NEW-PATH",
        "001c.733c.9337", "ztp_router",
    )
    assert_rejected(
        "001c.733c.9337", "TEST-R6-NEW-PATH", 403
    )
    assert ztp["calls"] == []
