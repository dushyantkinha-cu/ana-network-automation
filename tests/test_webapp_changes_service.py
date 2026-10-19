"""Unit tests for webapp.services.changes."""

from unittest.mock import Mock, call

import pytest

from webapp.services import changes as cs


# ------------------------------------------------------------
# Resource slug generation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("  New Site  ", "new-site"),
        ("WAN IPv6 / Update", "wan-ipv6-update"),
        ("R1___WAN", "r1-wan"),
        ("a--b", "a-b"),
        ("...", ""),
        ("  ", ""),
        ("site42", "site42"),
    ],
)
def test_make_slug(value, expected):
    assert cs.make_slug(value) == expected


# ------------------------------------------------------------
# Simulated managed-device data
# ------------------------------------------------------------

@pytest.fixture
def wan_device():
    return {
        "device_id": 123,
        "hostname": "R1",
        "config_context": {
            "nat": {
                "outside_interface": "Ethernet1",
            }
        },
    }


# ------------------------------------------------------------
# Device ID validation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "device",
    [
        {"hostname": "R1"},
        {"hostname": "R1", "device_id": 0},
    ],
)
def test_get_wan_address_state_requires_device_id(
    monkeypatch, device
):
    """A managed device must have a valid NetBox device ID."""

    netbox = Mock()
    monkeypatch.setattr(cs, "netbox_get", netbox)

    with pytest.raises(
        RuntimeError,
        match="Managed device has no NetBox device ID",
    ):
        cs.get_wan_address_state(device)

    netbox.assert_not_called()


# ------------------------------------------------------------
# Missing or incomplete NAT configuration
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "context",
    [
        None,
        {},
        {"nat": None},
        {"nat": {}},
        {"nat": {"outside_interface": ""}},
    ],
)
def test_get_wan_address_state_without_outside_interface(
    monkeypatch, context
):
    """Without an outside interface, no lookup is necessary."""

    netbox = Mock()
    monkeypatch.setattr(cs, "netbox_get", netbox)

    device = {
        "device_id": 123,
        "hostname": "R1",
        "config_context": context,
    }

    assert cs.get_wan_address_state(device) is None

    netbox.assert_not_called()


# ------------------------------------------------------------
# WAN interface lookup safeguards
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "interfaces",
    [
        [],
        [
            {"id": 11, "name": "Ethernet1"},
            {"id": 12, "name": "Ethernet1"},
        ],
    ],
)
def test_get_wan_address_state_requires_unique_interface(
    monkeypatch, wan_device, interfaces
):
    """Zero or multiple matching interfaces must be rejected."""

    netbox = Mock(
        return_value={"results": interfaces}
    )
    monkeypatch.setattr(cs, "netbox_get", netbox)

    with pytest.raises(
        RuntimeError,
        match="Unable to uniquely resolve R1 WAN interface Ethernet1",
    ):
        cs.get_wan_address_state(wan_device)

    netbox.assert_called_once_with(
        "/api/dcim/interfaces/"
        "?device_id=123"
        "&name=Ethernet1"
    )


# ------------------------------------------------------------
# Valid WAN interface without assigned IP addresses
# ------------------------------------------------------------

def test_get_wan_address_state_no_ip_addresses(
    monkeypatch, wan_device
):
    """A uniquely resolved WAN interface may have no IP addresses."""

    netbox = Mock(
        side_effect=[
            {
                "results": [
                    {
                        "id": 44,
                        "name": "Ethernet1",
                    }
                ]
            },
            {
                "results": [],
            },
        ]
    )

    monkeypatch.setattr(cs, "netbox_get", netbox)

    result = cs.get_wan_address_state(wan_device)

    assert result == {
        "interface_id": 44,
        "interface_name": "Ethernet1",
        "ipv4": None,
        "ipv6": None,
    }

    assert netbox.call_args_list == [
        call(
            "/api/dcim/interfaces/"
            "?device_id=123"
            "&name=Ethernet1"
        ),
        call(
            "/api/ipam/ip-addresses/"
            "?interface_id=44"
            "&limit=0"
        ),
    ]


# ------------------------------------------------------------
# WAN IPv4 and IPv6 address processing
# ------------------------------------------------------------

@pytest.fixture
def mock_wan_lookup(monkeypatch):
    """Simulate a unique WAN interface and its assigned IP addresses."""

    def setup(ip_objects):
        netbox = Mock(
            side_effect=[
                {
                    "results": [
                        {
                            "id": 44,
                            "name": "Ethernet1",
                        }
                    ]
                },
                {
                    "results": ip_objects,
                },
            ]
        )

        monkeypatch.setattr(cs, "netbox_get", netbox)

        return netbox

    return setup


@pytest.mark.parametrize(
    "ip_objects, expected_ipv4, expected_ipv6",
    [
        (
            [
                {"id": 10, "address": "192.0.2.14/27"},
            ],
            {
                "id": 10,
                "address": "192.0.2.14/27",
                "prefixlen": 27,
            },
            None,
        ),
        (
            [
                {"id": 20, "address": "2001:db8::14/64"},
            ],
            None,
            {
                "id": 20,
                "address": "2001:db8::14/64",
                "prefixlen": 64,
            },
        ),
        (
            [
                {"id": 10, "address": "192.0.2.14/27"},
                {"id": 20, "address": "2001:db8::14/64"},
            ],
            {
                "id": 10,
                "address": "192.0.2.14/27",
                "prefixlen": 27,
            },
            {
                "id": 20,
                "address": "2001:db8::14/64",
                "prefixlen": 64,
            },
        ),
        (
            [
                {"id": 20, "address": "2001:db8::14/64"},
                {"id": 10, "address": "192.0.2.14/27"},
            ],
            {
                "id": 10,
                "address": "192.0.2.14/27",
                "prefixlen": 27,
            },
            {
                "id": 20,
                "address": "2001:db8::14/64",
                "prefixlen": 64,
            },
        ),
    ],
)
def test_get_wan_address_state_valid_addresses(
    wan_device,
    mock_wan_lookup,
    ip_objects,
    expected_ipv4,
    expected_ipv6,
):
    """IPv4, IPv6 and dual-stack addresses must be resolved correctly."""

    netbox = mock_wan_lookup(ip_objects)

    result = cs.get_wan_address_state(wan_device)

    assert result == {
        "interface_id": 44,
        "interface_name": "Ethernet1",
        "ipv4": expected_ipv4,
        "ipv6": expected_ipv6,
    }

    assert netbox.call_args_list == [
        call(
            "/api/dcim/interfaces/"
            "?device_id=123"
            "&name=Ethernet1"
        ),
        call(
            "/api/ipam/ip-addresses/"
            "?interface_id=44"
            "&limit=0"
        ),
    ]


# ------------------------------------------------------------
# IP objects without addresses
# ------------------------------------------------------------

def test_get_wan_address_state_skips_missing_addresses(
    wan_device, mock_wan_lookup
):
    """Incomplete IP objects must not be interpreted as addresses."""

    mock_wan_lookup([
        {"id": 10},
        {"id": 11, "address": None},
        {"id": 12, "address": ""},
        {"id": 13, "address": "192.0.2.14/27"},
    ])

    result = cs.get_wan_address_state(wan_device)

    assert result["ipv4"] == {
        "id": 13,
        "address": "192.0.2.14/27",
        "prefixlen": 27,
    }
    assert result["ipv6"] is None


# ------------------------------------------------------------
# Duplicate WAN address safeguards
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "addresses, expected_error",
    [
        (
            [
                "192.0.2.10/27",
                "192.0.2.11/27",
            ],
            "WAN interface has multiple IPv4 addresses",
        ),
        (
            [
                "2001:db8::10/64",
                "2001:db8::11/64",
            ],
            "WAN interface has multiple IPv6 addresses",
        ),
    ],
)
def test_get_wan_address_state_rejects_duplicate_family(
    wan_device,
    mock_wan_lookup,
    addresses,
    expected_error,
):
    """Multiple addresses in one IP family must block automatic editing."""

    mock_wan_lookup([
        {"id": index, "address": address}
        for index, address in enumerate(addresses, start=10)
    ])

    with pytest.raises(RuntimeError, match=expected_error):
        cs.get_wan_address_state(wan_device)


# ------------------------------------------------------------
# Invalid IP address validation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "invalid_address",
    [
        "not-an-ip-address",
        "192.0.2.1/33",
        "2001:db8::1/129",
    ],
)
def test_get_wan_address_state_invalid_ip(
    wan_device,
    mock_wan_lookup,
    invalid_address,
):
    """Malformed IPv4 and IPv6 addresses must be rejected."""

    mock_wan_lookup([
        {
            "id": 10,
            "address": invalid_address,
        }
    ])

    with pytest.raises(ValueError):
        cs.get_wan_address_state(wan_device)


# ------------------------------------------------------------
# Incomplete NetBox API response
# ------------------------------------------------------------

def test_get_wan_address_state_missing_ip_results(
    monkeypatch, wan_device
):
    """An API response without results is treated as an empty list."""

    netbox = Mock(
        side_effect=[
            {
                "results": [
                    {"id": 44, "name": "Ethernet1"}
                ]
            },
            {},
        ]
    )

    monkeypatch.setattr(cs, "netbox_get", netbox)

    result = cs.get_wan_address_state(wan_device)

    assert result["interface_id"] == 44
    assert result["ipv4"] is None
    assert result["ipv6"] is None
    assert netbox.call_count == 2


# ------------------------------------------------------------
# NetBox request failures
# ------------------------------------------------------------

def test_get_wan_address_state_netbox_failure(
    monkeypatch, wan_device
):
    """NetBox API errors must propagate rather than be hidden."""

    netbox = Mock(
        side_effect=ConnectionError(
            "Simulated NetBox API outage"
        )
    )

    monkeypatch.setattr(cs, "netbox_get", netbox)

    with pytest.raises(
        ConnectionError,
        match="Simulated NetBox API outage",
    ):
        cs.get_wan_address_state(wan_device)

    netbox.assert_called_once()
