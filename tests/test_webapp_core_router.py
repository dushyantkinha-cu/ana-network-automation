"""Isolated unit tests for the FastAPI Core Router."""

import json
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import webapp.routers.core as core


# ------------------------------------------------------------
# Isolated test environment
# ------------------------------------------------------------

@pytest.fixture
def isolated_core(monkeypatch):
    devices = [
        {
            "hostname": "R1",
            "platform": "Arista",
            "config_profile": "router",
        },
        {
            "hostname": "R2",
            "platform": "Cisco",
            "config_profile": None,
        },
        {
            "hostname": "S1",
            "platform": "Arista",
            "config_profile": "switch",
        },
        {
            "hostname": "S2",
            "platform": None,
            "config_profile": "",
        },
    ]

    inventory = {
        "devices": devices,
        "source": "simulated",
    }

    device_reader = Mock(return_value=devices)
    inventory_reader = Mock(return_value=inventory)
    renderer = Mock()

    monkeypatch.setattr(
        core, "get_devices", device_reader
    )
    monkeypatch.setattr(
        core, "load_inventory", inventory_reader
    )
    monkeypatch.setattr(
        core, "NETBOX_URL", "https://netbox.example"
    )
    monkeypatch.setattr(
        core.templates,
        "TemplateResponse",
        renderer,
    )

    return {
        "devices": devices,
        "inventory": inventory,
        "device_reader": device_reader,
        "inventory_reader": inventory_reader,
        "renderer": renderer,
    }


# ------------------------------------------------------------
# GET /health
# ------------------------------------------------------------

def test_health_returns_expected_status():
    result = core.health()

    assert result == {
        "status": "ok",
        "application": "ANA Network Automation",
    }


# ------------------------------------------------------------
# GET /api/inventory - success
# ------------------------------------------------------------

def test_api_inventory_returns_json(
    isolated_core,
):
    result = core.api_inventory()

    assert result.status_code == 200
    assert result.media_type == "application/json"

    assert json.loads(result.body) == (
        isolated_core["inventory"]
    )

    isolated_core["inventory_reader"].assert_called_once_with()
    isolated_core["device_reader"].assert_not_called()


# ------------------------------------------------------------
# GET /api/inventory - service failure
# ------------------------------------------------------------

def test_api_inventory_handles_service_failure(
    isolated_core,
):
    original_error = RuntimeError(
        "Simulated inventory loading failure"
    )

    isolated_core["inventory_reader"].side_effect = (
        original_error
    )

    with pytest.raises(HTTPException) as exc:
        core.api_inventory()

    assert exc.value.status_code == 503
    assert exc.value.detail == str(original_error)
    assert exc.value.__cause__ is original_error

    isolated_core["renderer"].assert_not_called()


# ------------------------------------------------------------
# GET / - dashboard rendering and inventory counts
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "empty_inventory",
    [False, True],
)
def test_dashboard_renders_inventory_statistics(
    isolated_core,
    empty_inventory,
):
    env = isolated_core

    if empty_inventory:
        env["device_reader"].return_value = []
        expected_devices = []
        expected_platform_counts = {}
        expected_profile_counts = {}
    else:
        expected_devices = env["devices"]

        expected_platform_counts = {
            "Arista": 2,
            "Cisco": 1,
            "Unknown": 1,
        }

        expected_profile_counts = {
            "router": 1,
            "switch": 1,
            "Unassigned": 2,
        }

    request = Mock()

    result = core.dashboard(request=request)

    assert result is env["renderer"].return_value

    env["renderer"].assert_called_once_with(
        request=request,
        name="dashboard.html",
        context={
            "page_title": "Dashboard",
            "devices": expected_devices,
            "device_count": len(expected_devices),
            "platform_counts": expected_platform_counts,
            "profile_counts": expected_profile_counts,
            "netbox_url": "https://netbox.example",
        },
    )

    env["device_reader"].assert_called_once_with()
    env["inventory_reader"].assert_not_called()


# ------------------------------------------------------------
# GET / - inventory service failure
# ------------------------------------------------------------

def test_dashboard_handles_inventory_failure(
    isolated_core,
):
    original_error = RuntimeError(
        "Simulated device inventory failure"
    )

    isolated_core["device_reader"].side_effect = (
        original_error
    )

    with pytest.raises(HTTPException) as exc:
        core.dashboard(request=Mock())

    assert exc.value.status_code == 503
    assert exc.value.detail == str(original_error)
    assert exc.value.__cause__ is original_error

    isolated_core["renderer"].assert_not_called()


# ------------------------------------------------------------
# GET /inventory - successful rendering
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "empty_inventory",
    [False, True],
)
def test_inventory_page_renders_devices(
    isolated_core,
    empty_inventory,
):
    env = isolated_core

    expected_devices = (
        [] if empty_inventory else env["devices"]
    )

    env["device_reader"].return_value = (
        expected_devices
    )

    request = Mock()

    result = core.inventory_page(
        request=request
    )

    assert result is env["renderer"].return_value

    env["renderer"].assert_called_once_with(
        request=request,
        name="inventory.html",
        context={
            "page_title": "Managed Inventory",
            "devices": expected_devices,
            "netbox_url": "https://netbox.example",
        },
    )

    env["device_reader"].assert_called_once_with()
    env["inventory_reader"].assert_not_called()


# ------------------------------------------------------------
# GET /inventory - inventory service failure
# ------------------------------------------------------------

def test_inventory_page_handles_service_failure(
    isolated_core,
):
    original_error = RuntimeError(
        "Simulated inventory service outage"
    )

    isolated_core["device_reader"].side_effect = (
        original_error
    )

    with pytest.raises(HTTPException) as exc:
        core.inventory_page(
            request=Mock()
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == str(original_error)
    assert exc.value.__cause__ is original_error

    isolated_core["renderer"].assert_not_called()
