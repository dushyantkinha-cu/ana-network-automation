"""Unit tests for NetBox inventory retrieval and normalization."""

import json
import runpy
from pathlib import Path

import requests
import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "automation"
    / "netbox_inventory.py"
)

BASE_URL = "http://netbox.example.invalid"


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))

        if url not in self.responses:
            raise AssertionError(
                f"Unexpected API request: {url}"
            )

        return FakeResponse(self.responses[url])


def test_inventory_pagination_and_managed_filter(
    monkeypatch, capsys
):
    """Verify pagination and exclusion of unmanaged devices."""

    r1 = {
        "id": 1,
        "name": "R1",
        "platform": {"name": "Arista EOS"},
        "role": {"name": "Router"},
        "device_type": {},
        "site": {"id": 1},
        "status": {"value": "active"},
        "primary_ip4": {
            "address": "172.20.20.14/24"
        },
        "custom_fields": {
            "automation_managed": True,
            "config_profile": {
                "value": "distribution"
            },
            "routing_protocols": [
                {"value": "ospfv2"},
                {"value": "isis"},
            ],
        },
        "config_context": {},
    }

    r5 = {
        "id": 3,
        "name": "R5",
        "custom_fields": {
            "automation_managed": False
        },
    }

    first_url = (
        f"{BASE_URL}/api/dcim/devices/"
        "?include=config_context"
    )

    second_url = (
        f"{BASE_URL}/api/dcim/devices/"
        "?include=config_context&offset=1"
    )

    responses = {
        first_url: {
            "results": [r1],
            "next": second_url,
        },
        second_url: {
            "results": [r5],
            "next": None,
        },
    }

    endpoints = [
        "dcim/interfaces/",
        "ipam/ip-addresses/",
        "ipam/vlans/",
        "ipam/fhrp-groups/",
        "ipam/fhrp-group-assignments/",
    ]

    for endpoint in endpoints:
        responses[f"{BASE_URL}/api/{endpoint}"] = {
            "results": [],
            "next": None,
        }

    fake_session = FakeSession(responses)

    monkeypatch.setenv("NETBOX_URL", BASE_URL)
    monkeypatch.setenv("NETBOX_TOKEN", "fake-test-token")

    monkeypatch.setattr(
        requests,
        "Session",
        lambda: fake_session,
    )

    monkeypatch.setattr("sys.argv", [str(SCRIPT)])
    runpy.run_path(
        str(SCRIPT),
        run_name="__main__",
    )

    output = json.loads(capsys.readouterr().out)

    assert output["managed_device_count"] == 1
    assert len(output["devices"]) == 1

    device = output["devices"][0]

    assert device["hostname"] == "R1"
    assert device["platform"] == "Arista EOS"
    assert device["automation_managed"] is True
    assert device["config_profile"] == "distribution"

    assert device["routing_protocols"] == [
        "isis",
        "ospfv2",
    ]

    assert all(
        d["hostname"] != "R5"
        for d in output["devices"]
    )

    assert fake_session.headers["Authorization"] == (
        "Token fake-test-token"
    )

    assert len(fake_session.calls) == 7

    assert fake_session.calls[0] == (
        first_url,
        {"limit": 1000},
        15,
    )

    assert fake_session.calls[1] == (
        second_url,
        None,
        15,
    )


def test_inventory_normalizes_interfaces_vlans_and_fhrp(
    monkeypatch, capsys
):
    """Verify NetBox data is normalized correctly."""

    device = {
        "id": 1,
        "name": "R1",
        "platform": {
            "name": "Arista EOS",
            "manufacturer": {"name": "Arista Networks"},
        },
        "role": {"name": "Router"},
        "device_type": {},
        "site": {"id": 1},
        "status": "active",
        "primary_ip4": {"address": "172.20.20.14/24"},
        "custom_fields": {
            "automation_managed": True,
            "config_profile": "distribution",
            "routing_protocols": [
                "isis",
                {"value": "ospfv2"},
            ],
        },
        "config_context": {"router_id": "10.0.0.1"},
    }

    interfaces = [
        {
            "id": 102,
            "name": "Et2",
            "device": {"id": 1},
            "type": "1000base-t",
            "enabled": False,
            "mgmt_only": False,
            "description": None,
            "mode": "access",
            "untagged_vlan": 20,
            "tagged_vlans": [],
        },
        {
            "id": 101,
            "name": "Et1",
            "device": {"id": 1},
            "type": {"value": "1000base-t"},
            "enabled": True,
            "mgmt_only": False,
            "description": "Uplink",
            "mode": {"value": "tagged"},
            "untagged_vlan": {"vid": 10},
            "tagged_vlans": [
                {"vid": 30},
                {"vid": 20},
            ],
        },
    ]

    ip_addresses = [
        {
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": 101,
            "address": "10.0.0.1/31",
        },
        {
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": 101,
            "address": "2001:db8::1/127",
        },
    ]

    vlans = [
        {
            "site": {"id": 1},
            "status": {"value": "active"},
            "vid": 30,
            "name": "Data",
        },
        {
            "site": {"id": 1},
            "status": "active",
            "vid": 10,
            "name": "Users",
        },
        {
            "site": {"id": 1},
            "status": "reserved",
            "vid": 99,
            "name": "Reserved",
        },
    ]

    fhrp_groups = [
        {
            "id": 9,
            "ip_addresses": [
                {"address": "10.0.0.254/24"},
            ],
        },
    ]

    fhrp_assignments = [
        {
            "interface_type": "dcim.interface",
            "interface_id": 101,
            "group": {
                "id": 9,
                "protocol": "vrrp2",
                "group_id": 10,
            },
            "priority": 150,
        },
    ]

    datasets = {
        "dcim/devices/?include=config_context": [device],
        "dcim/interfaces/": interfaces,
        "ipam/ip-addresses/": ip_addresses,
        "ipam/vlans/": vlans,
        "ipam/fhrp-groups/": fhrp_groups,
        "ipam/fhrp-group-assignments/": fhrp_assignments,
    }

    responses = {
        f"{BASE_URL}/api/{endpoint}": {
            "results": records,
            "next": None,
        }
        for endpoint, records in datasets.items()
    }

    fake_session = FakeSession(responses)

    monkeypatch.setenv("NETBOX_URL", BASE_URL)
    monkeypatch.setenv("NETBOX_TOKEN", "fake-test-token")
    monkeypatch.setattr(
        requests, "Session", lambda: fake_session
    )

    monkeypatch.setattr("sys.argv", [str(SCRIPT)])
    runpy.run_path(str(SCRIPT), run_name="__main__")

    output = json.loads(capsys.readouterr().out)

    assert output["managed_device_count"] == 1

    result = output["devices"][0]

    assert result["manufacturer"] == "Arista Networks"
    assert result["status"] == "active"
    assert result["management_ip"] == "172.20.20.14/24"
    assert result["config_profile"] == "distribution"
    assert result["routing_protocols"] == ["isis", "ospfv2"]
    assert result["config_context"] == {
        "router_id": "10.0.0.1"
    }

    # Interfaces must be sorted by name.
    assert [
        item["name"] for item in result["interfaces"]
    ] == ["Et1", "Et2"]

    et1 = result["interfaces"][0]
    et2 = result["interfaces"][1]

    # Interface normalization.
    assert et1["type"] == "1000base-t"
    assert et1["description"] == "Uplink"
    assert et1["mode"] == "tagged"
    assert et1["untagged_vlan"] == 10
    assert et1["tagged_vlans"] == [20, 30]

    # IP addresses associated with Et1.
    assert et1["ip_addresses"] == [
        "10.0.0.1/31",
        "2001:db8::1/127",
    ]

    # FHRP / VRRP normalization.
    assert et1["fhrp_groups"] == [
        {
            "protocol": "vrrp2",
            "group_id": 10,
            "priority": 150,
            "vip_addresses": ["10.0.0.254/24"],
        }
    ]

    # Optional interface description defaults to empty.
    assert et2["description"] == ""
    assert et2["mode"] == "access"
    assert et2["untagged_vlan"] == 20

    # Only active VLANs should be included.
    assert result["vlans"] == [
        {"vid": 10, "name": "Users"},
        {"vid": 30, "name": "Data"},
    ]

    assert len(fake_session.calls) == 6


# ------------------------------------------------------------
# Environment and API error handling
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "missing_variable, expected_error",
    [
        ("NETBOX_URL", "NETBOX_URL is not set."),
        ("NETBOX_TOKEN", "NETBOX_TOKEN is not set."),
    ],
)
def test_missing_netbox_credentials(
    monkeypatch, capsys, missing_variable, expected_error
):
    """Missing NetBox configuration must fail safely."""

    monkeypatch.setenv("NETBOX_URL", BASE_URL)
    monkeypatch.setenv("NETBOX_TOKEN", "fake-test-token")
    monkeypatch.delenv(missing_variable)

    def forbidden_session():
        raise AssertionError(
            "No HTTP session should be created"
        )

    monkeypatch.setattr(
        requests, "Session", forbidden_session
    )

    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert exc.value.code == 1
    assert expected_error in capsys.readouterr().err


def test_netbox_api_connection_failure(monkeypatch, capsys):
    """An API connection failure must produce a clear error."""

    monkeypatch.setenv("NETBOX_URL", BASE_URL)
    monkeypatch.setenv("NETBOX_TOKEN", "fake-test-token")

    fake_session = FakeSession({})

    def failed_request(*args, **kwargs):
        raise requests.ConnectionError(
            "Simulated NetBox connection failure"
        )

    monkeypatch.setattr(
        fake_session, "get", failed_request
    )

    monkeypatch.setattr(
        requests, "Session", lambda: fake_session
    )

    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert exc.value.code == 1

    error = capsys.readouterr().err

    assert "NetBox API request failed" in error
    assert "Simulated NetBox connection failure" in error
