"""Unit tests for webapp.services.inventory."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from webapp.services import inventory as inv


# ------------------------------------------------------------
# Isolated inventory environment
# ------------------------------------------------------------

@pytest.fixture
def isolated_inventory(monkeypatch, tmp_path):
    """Prevent execution of the real inventory script."""

    monkeypatch.setattr(
        inv,
        "INVENTORY_SCRIPT",
        tmp_path / "fake-netbox-inventory.py",
    )

    monkeypatch.setattr(
        inv,
        "REPO_ROOT",
        tmp_path,
    )

    return tmp_path


# ------------------------------------------------------------
# Successful inventory retrieval
# ------------------------------------------------------------

def test_load_inventory_success(
    monkeypatch,
    isolated_inventory,
):
    expected = {
        "devices": [
            {
                "hostname": "R1",
                "platform": "arista",
            },
            {
                "hostname": "R2",
                "platform": "cisco",
            },
        ]
    }

    runner = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps(expected),
            stderr="",
        )
    )

    monkeypatch.setattr(
        inv.subprocess,
        "run",
        runner,
    )

    assert inv.load_inventory() == expected

    runner.assert_called_once_with(
        [
            inv.sys.executable,
            str(inv.INVENTORY_SCRIPT),
        ],
        capture_output=True,
        text=True,
        cwd=isolated_inventory,
        env=inv.os.environ,
    )


# ------------------------------------------------------------
# Inventory script failure handling
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "stderr, expected_error",
    [
        (
            "  Simulated NetBox failure  \n",
            "Simulated NetBox failure",
        ),
        (
            "",
            "Unable to retrieve NetBox inventory.",
        ),
    ],
)
def test_load_inventory_script_failure(
    monkeypatch,
    isolated_inventory,
    stderr,
    expected_error,
):
    runner = Mock(
        return_value=SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=stderr,
        )
    )

    monkeypatch.setattr(
        inv.subprocess,
        "run",
        runner,
    )

    with pytest.raises(RuntimeError) as exc:
        inv.load_inventory()

    assert str(exc.value) == expected_error
    runner.assert_called_once()


def test_load_inventory_invalid_json(
    monkeypatch,
    isolated_inventory,
):
    runner = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout="{invalid JSON",
            stderr="",
        )
    )

    monkeypatch.setattr(
        inv.subprocess,
        "run",
        runner,
    )

    with pytest.raises(
        RuntimeError,
        match="Inventory script returned invalid JSON",
    ) as exc:
        inv.load_inventory()

    assert isinstance(
        exc.value.__cause__,
        json.JSONDecodeError,
    )


# ------------------------------------------------------------
# Managed devices
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "inventory, expected",
    [
        (
            {
                "devices": [
                    {"hostname": "R1"},
                    {"hostname": "S1"},
                ]
            },
            [
                {"hostname": "R1"},
                {"hostname": "S1"},
            ],
        ),
        (
            {},
            [],
        ),
    ],
)
def test_get_devices(
    monkeypatch,
    inventory,
    expected,
):
    loader = Mock(return_value=inventory)

    monkeypatch.setattr(
        inv,
        "load_inventory",
        loader,
    )

    assert inv.get_devices() == expected

    loader.assert_called_once_with()


# ------------------------------------------------------------
# NetBox sites
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "api_data, expected",
    [
        (
            {
                "results": [
                    {
                        "id": 10,
                        "name": "Boulder",
                        "slug": "boulder",
                    },
                    {
                        "id": 20,
                        "name": "Denver",
                        "slug": "denver",
                    },
                ]
            },
            [
                {"id": 10, "name": "Boulder"},
                {"id": 20, "name": "Denver"},
            ],
        ),
        (
            {"results": []},
            [],
        ),
        (
            {},
            [],
        ),
    ],
)
def test_get_sites(
    monkeypatch,
    api_data,
    expected,
):
    netbox = Mock(return_value=api_data)

    monkeypatch.setattr(
        inv,
        "netbox_get",
        netbox,
    )

    assert inv.get_sites() == expected

    netbox.assert_called_once_with(
        "/api/dcim/sites/?limit=0"
    )


# ------------------------------------------------------------
# Device types and platforms
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "function_name, endpoint",
    [
        (
            "get_device_types",
            "/api/dcim/device-types/?limit=0",
        ),
        (
            "get_platforms",
            "/api/dcim/platforms/?limit=0",
        ),
    ],
)
def test_network_vendor_filtering(
    monkeypatch,
    function_name,
    endpoint,
):
    """Only items from configured network vendors are accepted."""

    allowed_vendor = next(iter(inv.NETWORK_VENDORS))

    allowed_item = {
        "id": 1,
        "manufacturer": {
            "name": allowed_vendor,
        },
    }

    api_data = {
        "results": [
            allowed_item,
            {
                "id": 2,
                "manufacturer": {
                    "name": "Unrecognized Test Vendor",
                },
            },
            {
                "id": 3,
                "manufacturer": None,
            },
            {
                "id": 4,
                "manufacturer": {},
            },
            {
                "id": 5,
            },
        ]
    }

    netbox = Mock(return_value=api_data)

    monkeypatch.setattr(
        inv,
        "netbox_get",
        netbox,
    )

    function = getattr(inv, function_name)

    assert function() == [allowed_item]

    netbox.assert_called_once_with(endpoint)


@pytest.mark.parametrize(
    "function_name",
    [
        "get_device_types",
        "get_platforms",
    ],
)
def test_network_vendor_filtering_empty_response(
    monkeypatch,
    function_name,
):
    netbox = Mock(return_value={})

    monkeypatch.setattr(
        inv,
        "netbox_get",
        netbox,
    )

    function = getattr(inv, function_name)

    assert function() == []

    netbox.assert_called_once()


# ------------------------------------------------------------
# Network device roles
# ------------------------------------------------------------

def test_get_network_roles_filters_allowed_roles(
    monkeypatch,
):
    router = {
        "id": 1,
        "name": "Router",
    }

    multilayer = {
        "id": 2,
        "name": "Multilayer Switch",
    }

    netbox = Mock(
        return_value={
            "results": [
                router,
                multilayer,
                {
                    "id": 3,
                    "name": "Server",
                },
                {
                    "id": 4,
                    "name": "Access Switch",
                },
                {
                    "id": 5,
                },
            ]
        }
    )

    monkeypatch.setattr(
        inv,
        "netbox_get",
        netbox,
    )

    assert inv.get_network_roles() == [
        router,
        multilayer,
    ]

    netbox.assert_called_once_with(
        "/api/dcim/device-roles/?limit=0"
    )


def test_get_network_roles_empty_response(
    monkeypatch,
):
    netbox = Mock(return_value={})

    monkeypatch.setattr(
        inv,
        "netbox_get",
        netbox,
    )

    assert inv.get_network_roles() == []

    netbox.assert_called_once_with(
        "/api/dcim/device-roles/?limit=0"
    )


# ------------------------------------------------------------
# Staged device filtering
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "api_data, expected_ids",
    [
        (
            {
                "results": [
                    {
                        "id": 1,
                        "custom_fields": {
                            "automation_managed": False,
                        },
                    },
                    {
                        "id": 2,
                        "custom_fields": {
                            "automation_managed": True,
                        },
                    },
                    {
                        "id": 3,
                        "custom_fields": {
                            "automation_managed": None,
                        },
                    },
                    {
                        "id": 4,
                        "custom_fields": {},
                    },
                    {
                        "id": 5,
                        "custom_fields": None,
                    },
                    {
                        "id": 6,
                    },
                    {
                        "id": 7,
                        "custom_fields": {
                            "automation_managed": 0,
                        },
                    },
                    {
                        "id": 8,
                        "custom_fields": {
                            "automation_managed": False,
                            "notes": "Unmanaged staged device",
                        },
                    },
                ]
            },
            [1, 8],
        ),
        (
            {"results": []},
            [],
        ),
        (
            {},
            [],
        ),
        (
            {
                "results": [
                    {
                        "id": 99,
                        "custom_fields": {
                            "automation_managed": False,
                        },
                    },
                ]
            },
            [99],
        ),
    ],
)
def test_get_staged_devices_filtering(
    monkeypatch,
    api_data,
    expected_ids,
):
    """Only explicitly unmanaged staged devices are returned."""

    netbox = Mock(return_value=api_data)

    monkeypatch.setattr(
        inv,
        "netbox_get",
        netbox,
    )

    result = inv.get_staged_devices()

    assert [device["id"] for device in result] == expected_ids

    netbox.assert_called_once_with(
        "/api/dcim/devices/?status=staged"
        "&include=config_context"
        "&limit=0"
    )


def test_get_staged_devices_netbox_failure(monkeypatch):
    """NetBox lookup failures must propagate to the caller."""

    netbox = Mock(
        side_effect=RuntimeError(
            "Simulated NetBox API failure"
        )
    )

    monkeypatch.setattr(
        inv,
        "netbox_get",
        netbox,
    )

    with pytest.raises(
        RuntimeError,
        match="Simulated NetBox API failure",
    ):
        inv.get_staged_devices()

    netbox.assert_called_once()


# ------------------------------------------------------------
# NetBox custom-field choice sets
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "api_data, expected",
    [
        (
            {
                "base_choices": [
                    ["arista", "Arista EOS"],
                    ["cisco", "Cisco IOS-XE"],
                ],
            },
            [
                {"value": "arista", "label": "Arista EOS"},
                {"value": "cisco", "label": "Cisco IOS-XE"},
            ],
        ),
        (
            {
                "extra_choices": [
                    ["nokia", "Nokia SR Linux"],
                ],
            },
            [
                {"value": "nokia", "label": "Nokia SR Linux"},
            ],
        ),
        (
            {
                "base_choices": [
                    ["router", "Router"],
                ],
                "extra_choices": [
                    ["switch", "Switch"],
                ],
            },
            [
                {"value": "router", "label": "Router"},
                {"value": "switch", "label": "Switch"},
            ],
        ),
        (
            {
                "base_choices": None,
                "extra_choices": None,
            },
            [],
        ),
        (
            {},
            [],
        ),
    ],
)
def test_get_choice_values(
    monkeypatch,
    api_data,
    expected,
):
    """Base and extra choices must be combined in order."""

    netbox = Mock(return_value=api_data)

    monkeypatch.setattr(
        inv,
        "netbox_get",
        netbox,
    )

    result = inv.get_choice_values(42)

    assert result == expected

    netbox.assert_called_once_with(
        "/api/extras/custom-field-choice-sets/42/"
    )


# ------------------------------------------------------------
# Managed device lookup
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "devices, hostname, expected",
    [
        (
            [
                {"hostname": "R1", "platform": "arista"},
                {"hostname": "R2", "platform": "cisco"},
            ],
            "R2",
            {"hostname": "R2", "platform": "cisco"},
        ),
        (
            [
                {"hostname": "R1"},
                {"hostname": "R2"},
            ],
            "R999",
            None,
        ),
        (
            [],
            "R1",
            None,
        ),
        (
            [
                {"hostname": "R1"},
                {"hostname": "r1"},
            ],
            "r1",
            {"hostname": "r1"},
        ),
    ],
)
def test_find_managed_device(
    monkeypatch,
    devices,
    hostname,
    expected,
):
    """Managed device lookup must match hostnames exactly."""

    loader = Mock(return_value=devices)

    monkeypatch.setattr(
        inv,
        "get_devices",
        loader,
    )

    result = inv.find_managed_device(hostname)

    assert result == expected

    loader.assert_called_once_with()
