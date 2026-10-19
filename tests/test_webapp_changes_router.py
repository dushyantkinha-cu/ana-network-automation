"""Additional isolated unit tests for the Changes Router."""

from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from starlette.datastructures import FormData

import webapp.routers.changes as cr


# ------------------------------------------------------------
# Isolated Changes Router environment
# ------------------------------------------------------------

@pytest.fixture
def isolated_changes(monkeypatch):
    """Mock inventory, NetBox, validation and template rendering."""

    managed_device = {
        "hostname": "R1",
        "device_id": 11,
        "platform": "Test Platform",
        "automation_managed": True,
        "config_profile": "router",
    }

    monkeypatch.setattr(
        cr,
        "NETBOX_URL",
        "https://netbox.example",
    )
    monkeypatch.setattr(
        cr,
        "ROUTING_CHOICE_SET_ID",
        101,
    )
    monkeypatch.setattr(
        cr,
        "PROFILE_CHOICE_SET_ID",
        202,
    )
    monkeypatch.setattr(
        cr,
        "PROFILE_TEMPLATE_MAP",
        {
            "Test Platform": {
                "router": "templates/test-router.j2",
                "switch": "templates/test-switch.j2",
            },
        },
    )

    devices = Mock(return_value=[managed_device])
    sites = Mock(
        return_value=[
            {"id": 10, "name": "Test Site"},
        ]
    )

    routing_choices = [
        {"value": "ospf", "label": "OSPF"},
    ]

    profile_choices = [
        {"value": "router", "label": "Router"},
        {"value": "switch", "label": "Switch"},
        {"value": "unsupported", "label": "Unsupported"},
    ]

    def choice_lookup(choice_set_id):
        if choice_set_id == 101:
            return routing_choices
        if choice_set_id == 202:
            return profile_choices
        raise AssertionError("Unexpected choice-set ID")

    choices = Mock(side_effect=choice_lookup)

    monkeypatch.setattr(cr, "get_devices", devices)
    monkeypatch.setattr(cr, "get_sites", sites)
    monkeypatch.setattr(cr, "get_choice_values", choices)

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "Unexpected external operation during unit testing"
        )

    netbox_get = Mock(side_effect=forbidden)
    netbox_patch = Mock(side_effect=forbidden)
    validator = Mock(side_effect=forbidden)
    wan_lookup = Mock(side_effect=forbidden)

    monkeypatch.setattr(cr, "netbox_get", netbox_get)
    monkeypatch.setattr(cr, "netbox_patch", netbox_patch)
    monkeypatch.setattr(cr, "run_validation", validator)
    monkeypatch.setattr(cr, "get_wan_address_state", wan_lookup)

    renderer = Mock()

    monkeypatch.setattr(
        cr.templates,
        "TemplateResponse",
        renderer,
    )

    return {
        "device": managed_device,
        "devices": devices,
        "sites": sites,
        "choices": choices,
        "routing_choices": routing_choices,
        "profile_choices": profile_choices,
        "netbox_get": netbox_get,
        "netbox_patch": netbox_patch,
        "validator": validator,
        "wan_lookup": wan_lookup,
        "renderer": renderer,
    }


# ------------------------------------------------------------
# Safe redirect destinations
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "form, expected",
    [
        (FormData([]), "/changes"),
        (
            FormData([("return_to", "/changes")]),
            "/changes",
        ),
        (
            FormData([("return_to", "/automation")]),
            "/automation",
        ),
        (
            FormData([("return_to", " /automation ")]),
            "/automation",
        ),
        (
            FormData([("return_to", "")]),
            "/changes",
        ),
        (
            FormData([("return_to", "//evil.example")]),
            "/changes",
        ),
        (
            FormData([
                ("return_to", "https://evil.example")
            ]),
            "/changes",
        ),
        (
            FormData([
                ("return_to", "/changes?device=R1")
            ]),
            "/changes",
        ),
    ],
)
def test_safe_return_to_prevents_unapproved_redirects(
    form,
    expected,
):
    """Only the two explicitly approved destinations are allowed."""

    assert cr._safe_return_to(form) == expected


# ------------------------------------------------------------
# Automatic validation feedback
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "returncode, expected_status",
    [
        (0, "success"),
        (1, "error"),
        (None, "error"),
    ],
)
def test_automatic_validation_feedback(
    monkeypatch,
    isolated_changes,
    returncode,
    expected_status,
):
    validator = Mock(
        return_value={
            "returncode": returncode,
            "stdout": "Simulated validation",
            "stderr": "",
        }
    )

    monkeypatch.setattr(cr, "run_validation", validator)

    status, message = cr._automatic_validation_feedback(
        "R1",
        "Intent was updated.",
    )

    assert status == expected_status
    assert message.startswith("Intent was updated.")

    if expected_status == "success":
        assert "Automatic validation passed for R1." in message
    else:
        assert "Automatic validation detected drift" in message
        assert "No deployment was performed." in message

    validator.assert_called_once_with(hostname="R1")


# ------------------------------------------------------------
# Changes page: unknown or unmanaged device
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "hostname",
    ["R5", "R999"],
)
def test_changes_page_rejects_unknown_device(
    isolated_changes,
    hostname,
):
    """Only devices present in managed inventory may be selected."""

    with pytest.raises(HTTPException) as exc:
        cr.changes_page(
            request=Mock(),
            device=hostname,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == (
        "Device is not in managed automation scope."
    )

    isolated_changes["netbox_get"].assert_not_called()
    isolated_changes["renderer"].assert_not_called()


# ------------------------------------------------------------
# Changes page: missing NetBox device ID
# ------------------------------------------------------------

def test_changes_page_rejects_missing_device_id(
    isolated_changes,
):
    isolated_changes["device"]["device_id"] = None

    with pytest.raises(HTTPException) as exc:
        cr.changes_page(
            request=Mock(),
            device="R1",
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == (
        "Managed device has no NetBox device ID."
    )

    isolated_changes["netbox_get"].assert_not_called()
    isolated_changes["renderer"].assert_not_called()


# ------------------------------------------------------------
# Changes page: NetBox read failure
# ------------------------------------------------------------

def test_changes_page_handles_netbox_read_failure(
    isolated_changes,
):
    original_error = RuntimeError(
        "Simulated NetBox read failure"
    )

    isolated_changes["netbox_get"].side_effect = (
        original_error
    )

    with pytest.raises(HTTPException) as exc:
        cr.changes_page(
            request=Mock(),
            device="R1",
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == str(original_error)
    assert exc.value.__cause__ is original_error

    isolated_changes["netbox_get"].assert_called_once_with(
        "/api/dcim/devices/11/"
    )

    isolated_changes["renderer"].assert_not_called()


# ------------------------------------------------------------
# Changes page: service failures
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "failure_source",
    [
        "get_devices",
        "get_sites",
        "routing_choices",
        "profile_choices",
    ],
)
def test_changes_page_handles_service_failure(
    isolated_changes,
    failure_source,
):
    original_error = RuntimeError(
        "Simulated inventory service failure"
    )

    if failure_source == "get_devices":
        isolated_changes["devices"].side_effect = (
            original_error
        )

    elif failure_source == "get_sites":
        isolated_changes["sites"].side_effect = (
            original_error
        )

    elif failure_source == "routing_choices":
        isolated_changes["choices"].side_effect = (
            original_error
        )

    else:
        isolated_changes["choices"].side_effect = [
            isolated_changes["routing_choices"],
            original_error,
        ]

    with pytest.raises(HTTPException) as exc:
        cr.changes_page(
            request=Mock(),
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == str(original_error)
    assert exc.value.__cause__ is original_error

    isolated_changes["netbox_get"].assert_not_called()
    isolated_changes["renderer"].assert_not_called()


# ------------------------------------------------------------
# Changes page: compatible profile filtering
# ------------------------------------------------------------

def test_changes_page_filters_compatible_profiles(
    isolated_changes,
):
    raw_device = {
        "id": 11,
        "name": "R1",
        "status": "active",
    }

    wan_state = {
        "interface_id": 44,
        "interface_name": "Ethernet1",
        "ipv4": None,
        "ipv6": None,
    }

    isolated_changes["netbox_get"].side_effect = None
    isolated_changes["netbox_get"].return_value = (
        raw_device
    )

    isolated_changes["wan_lookup"].side_effect = None
    isolated_changes["wan_lookup"].return_value = (
        wan_state
    )

    request = Mock()

    result = cr.changes_page(
        request=request,
        device="R1",
        status="success",
        message="Simulated message",
    )

    renderer = isolated_changes["renderer"]

    assert result is renderer.return_value

    kwargs = renderer.call_args.kwargs
    context = kwargs["context"]

    assert kwargs["request"] is request
    assert kwargs["name"] == "changes.html"

    assert context["selected_device"] == (
        isolated_changes["device"]
    )
    assert context["raw_device"] == raw_device

    assert context["compatible_profiles"] == [
        {"value": "router", "label": "Router"},
        {"value": "switch", "label": "Switch"},
    ]

    assert context["template_path"] == (
        "templates/test-router.j2"
    )

    assert context["wan_state"] == wan_state
    assert context["action_status"] == "success"
    assert context["action_message"] == (
        "Simulated message"
    )

    isolated_changes["netbox_get"].assert_called_once_with(
        "/api/dcim/devices/11/"
    )

    isolated_changes["wan_lookup"].assert_called_once_with(
        isolated_changes["device"]
    )

    isolated_changes["netbox_patch"].assert_not_called()
    isolated_changes["validator"].assert_not_called()


# ------------------------------------------------------------
# Mocked POST request helpers
# ------------------------------------------------------------

import asyncio
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit


def make_intent_request(**overrides):
    fields = {
        "hostname": "R1",
        "config_profile": "router",
        "return_to": "/changes",
        "routing_protocols": ["ospf"],
    }

    fields.update(overrides)

    pairs = [
        (key, value)
        for key, value in fields.items()
        if key != "routing_protocols"
    ]

    for protocol in fields["routing_protocols"]:
        pairs.append(("routing_protocols", protocol))

    request = Mock()
    request.form = AsyncMock(
        return_value=FormData(pairs)
    )

    return request


def submit_intent(**overrides):
    return asyncio.run(
        cr.update_device_intent(
            make_intent_request(**overrides)
        )
    )


@pytest.fixture
def isolated_intent(monkeypatch, isolated_changes):
    """Use mocked managed-device lookup for intent updates."""

    finder = Mock(
        return_value=isolated_changes["device"]
    )

    monkeypatch.setattr(
        cr,
        "find_managed_device",
        finder,
    )

    isolated_changes["finder"] = finder

    return isolated_changes


# ------------------------------------------------------------
# Reject devices outside managed scope
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "device",
    [
        None,
        {
            "hostname": "R5",
            "device_id": 15,
            "automation_managed": False,
        },
        {
            "hostname": "R1",
            "device_id": 11,
            "automation_managed": 0,
        },
    ],
)
def test_intent_rejects_unmanaged_device(
    isolated_intent,
    device,
):
    isolated_intent["finder"].return_value = device

    with pytest.raises(HTTPException) as exc:
        submit_intent()

    assert exc.value.status_code == 403
    assert exc.value.detail == (
        "Device is outside managed automation scope."
    )

    isolated_intent["netbox_patch"].assert_not_called()
    isolated_intent["validator"].assert_not_called()


# ------------------------------------------------------------
# Reject unsupported routing protocols
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "protocols",
    [
        ["bgp"],
        ["ospf", "unsupported"],
    ],
)
def test_intent_rejects_invalid_routing(
    isolated_intent,
    protocols,
):
    with pytest.raises(HTTPException) as exc:
        submit_intent(
            routing_protocols=protocols
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "Invalid routing protocol selection."
    )

    isolated_intent["netbox_patch"].assert_not_called()
    isolated_intent["validator"].assert_not_called()


# ------------------------------------------------------------
# Reject unsupported configuration profiles
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "profile",
    ["", "unsupported-profile"],
)
def test_intent_rejects_unsupported_profile(
    isolated_intent,
    profile,
):
    with pytest.raises(HTTPException) as exc:
        submit_intent(
            config_profile=profile
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "Selected configuration profile "
        "is not supported by this platform."
    )

    isolated_intent["netbox_patch"].assert_not_called()
    isolated_intent["validator"].assert_not_called()


# ------------------------------------------------------------
# Missing NetBox device ID
# ------------------------------------------------------------

def test_intent_requires_netbox_device_id(
    isolated_intent,
):
    isolated_intent["device"]["device_id"] = None

    with pytest.raises(HTTPException) as exc:
        submit_intent()

    assert exc.value.status_code == 500
    assert exc.value.detail == (
        "Managed device has no NetBox device ID."
    )

    isolated_intent["netbox_patch"].assert_not_called()
    isolated_intent["validator"].assert_not_called()


# ------------------------------------------------------------
# Inventory and choice-set lookup errors
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "failure_source",
    ["device_lookup", "routing_choices"],
)
def test_intent_handles_service_failure(
    monkeypatch,
    isolated_intent,
    failure_source,
):
    error = RuntimeError(
        "Simulated inventory service failure"
    )

    if failure_source == "device_lookup":
        isolated_intent["finder"].side_effect = error
    else:
        isolated_intent["choices"].side_effect = error

    with pytest.raises(HTTPException) as exc:
        submit_intent()

    assert exc.value.status_code == 503
    assert exc.value.detail == str(error)
    assert exc.value.__cause__ is error

    isolated_intent["netbox_patch"].assert_not_called()
    isolated_intent["validator"].assert_not_called()


# ------------------------------------------------------------
# NetBox PATCH errors
# ------------------------------------------------------------

def test_intent_handles_netbox_patch_failure(
    isolated_intent,
):
    error = RuntimeError(
        "Simulated NetBox PATCH failure"
    )

    isolated_intent["netbox_patch"].side_effect = error

    with pytest.raises(HTTPException) as exc:
        submit_intent()

    assert exc.value.status_code == 503
    assert exc.value.detail == str(error)
    assert exc.value.__cause__ is error

    isolated_intent["netbox_patch"].assert_called_once()
    isolated_intent["validator"].assert_not_called()


# ------------------------------------------------------------
# Successful intent update with automatic validation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "returncode, return_to, expected_path, expected_status",
    [
        (0, "/changes", "/changes", "success"),
        (1, "/automation", "/automation", "error"),
        (
            1,
            "https://untrusted.example",
            "/changes",
            "error",
        ),
    ],
)
def test_intent_update_and_validation_feedback(
    isolated_intent,
    returncode,
    return_to,
    expected_path,
    expected_status,
):
    patch = isolated_intent["netbox_patch"]
    validator = isolated_intent["validator"]

    patch.side_effect = None
    patch.return_value = {"id": 11}

    validator.side_effect = None
    validator.return_value = {
        "returncode": returncode,
        "stdout": "Simulated validation",
        "stderr": "",
    }

    result = submit_intent(
        return_to=return_to
    )

    assert result.status_code == 303

    redirect = urlsplit(result.headers["location"])
    params = parse_qs(redirect.query)

    assert redirect.path == expected_path
    assert params["device"] == ["R1"]
    assert params["status"] == [expected_status]

    message = params["message"][0]

    assert "NetBox intent for R1 was updated" in message

    if returncode == 0:
        assert "Automatic validation passed" in message
    else:
        assert "No deployment was performed." in message

    patch.assert_called_once_with(
        "/api/dcim/devices/11/",
        {
            "custom_fields": {
                "automation_managed": True,
                "routing_protocols": ["ospf"],
                "config_profile": "router",
            },
        },
    )

    validator.assert_called_once_with(
        hostname="R1"
    )


# ------------------------------------------------------------
# Multiple permitted routing protocols
# ------------------------------------------------------------

def test_intent_accepts_multiple_allowed_protocols(
    isolated_intent,
):
    isolated_intent["routing_choices"].append(
        {
            "value": "bgp",
            "label": "BGP",
        }
    )

    patch = isolated_intent["netbox_patch"]
    validator = isolated_intent["validator"]

    patch.side_effect = None
    validator.side_effect = None
    validator.return_value = {"returncode": 0}

    result = submit_intent(
        routing_protocols=["ospf", "bgp"]
    )

    assert result.status_code == 303

    patch.assert_called_once_with(
        "/api/dcim/devices/11/",
        {
            "custom_fields": {
                "automation_managed": True,
                "routing_protocols": ["ospf", "bgp"],
                "config_profile": "router",
            },
        },
    )

    validator.assert_called_once_with(
        hostname="R1"
    )


# ------------------------------------------------------------
# WAN address update test helpers
# ------------------------------------------------------------

from unittest.mock import call


def make_wan_request(**overrides):
    fields = {
        "hostname": "R1",
        "wan_ipv4": "198.51.100.50/27",
        "wan_ipv6": "2001:db8::50/64",
        "return_to": "/changes",
    }

    fields.update(overrides)

    request = Mock()
    request.form = AsyncMock(
        return_value=FormData(list(fields.items()))
    )

    return request


def submit_wan(**overrides):
    return asyncio.run(
        cr.update_wan_addresses(
            make_wan_request(**overrides)
        )
    )


@pytest.fixture
def isolated_wan(monkeypatch, isolated_changes):
    """Provide a simulated managed device and dual-stack WAN."""

    finder = Mock(
        return_value=isolated_changes["device"]
    )

    monkeypatch.setattr(
        cr,
        "find_managed_device",
        finder,
    )

    wan_state = {
        "interface_id": 44,
        "interface_name": "Ethernet1",
        "ipv4": {
            "id": 201,
            "address": "198.51.100.10/27",
            "prefixlen": 27,
        },
        "ipv6": {
            "id": 202,
            "address": "2001:db8::10/64",
            "prefixlen": 64,
        },
    }

    wan_lookup = isolated_changes["wan_lookup"]
    wan_lookup.side_effect = None
    wan_lookup.return_value = wan_state

    isolated_changes["finder"] = finder
    isolated_changes["wan_state"] = wan_state

    return isolated_changes


# ------------------------------------------------------------
# Reject unmanaged devices
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "device",
    [
        None,
        {
            "hostname": "R5",
            "automation_managed": False,
        },
        {
            "hostname": "R1",
            "automation_managed": 0,
        },
    ],
)
def test_wan_rejects_unmanaged_device(
    isolated_wan,
    device,
):
    isolated_wan["finder"].return_value = device

    with pytest.raises(HTTPException) as exc:
        submit_wan()

    assert exc.value.status_code == 403
    assert exc.value.detail == (
        "Device is outside managed automation scope."
    )

    isolated_wan["wan_lookup"].assert_not_called()
    isolated_wan["netbox_patch"].assert_not_called()
    isolated_wan["validator"].assert_not_called()


# ------------------------------------------------------------
# Missing WAN interface or address objects
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "missing, expected_error",
    [
        (
            "state",
            "This device has no managed WAN interface.",
        ),
        (
            "ipv4",
            "WAN interface has no IPv4 address object.",
        ),
        (
            "ipv6",
            "WAN interface has no IPv6 address object.",
        ),
    ],
)
def test_wan_requires_existing_address_objects(
    isolated_wan,
    missing,
    expected_error,
):
    if missing == "state":
        isolated_wan["wan_lookup"].return_value = None
    else:
        isolated_wan["wan_state"][missing] = None

    with pytest.raises(HTTPException) as exc:
        submit_wan()

    assert exc.value.status_code == 400
    assert exc.value.detail == expected_error

    isolated_wan["netbox_patch"].assert_not_called()
    isolated_wan["validator"].assert_not_called()


# ------------------------------------------------------------
# Invalid CIDR notation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "field, invalid_value",
    [
        ("wan_ipv4", "invalid-ip"),
        ("wan_ipv6", "not-an-ipv6-address"),
    ],
)
def test_wan_rejects_invalid_cidr(
    isolated_wan,
    field,
    invalid_value,
):
    with pytest.raises(HTTPException) as exc:
        submit_wan(**{field: invalid_value})

    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "WAN addresses must use valid CIDR notation."
    )

    isolated_wan["netbox_patch"].assert_not_called()
    isolated_wan["validator"].assert_not_called()


# ------------------------------------------------------------
# IPv4 and IPv6 field validation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "field, invalid_value, expected_error",
    [
        (
            "wan_ipv4",
            "2001:db8::50/64",
            "WAN IPv4 field must contain an IPv4 address.",
        ),
        (
            "wan_ipv6",
            "198.51.100.50/27",
            "WAN IPv6 field must contain an IPv6 address.",
        ),
    ],
)
def test_wan_rejects_wrong_address_family(
    isolated_wan,
    field,
    invalid_value,
    expected_error,
):
    with pytest.raises(HTTPException) as exc:
        submit_wan(**{field: invalid_value})

    assert exc.value.status_code == 400
    assert exc.value.detail == expected_error

    isolated_wan["netbox_patch"].assert_not_called()
    isolated_wan["validator"].assert_not_called()


# ------------------------------------------------------------
# Existing prefix-length preservation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "field, changed_address, expected_error",
    [
        (
            "wan_ipv4",
            "198.51.100.50/24",
            "WAN IPv4 prefix length cannot "
            "be changed from this form.",
        ),
        (
            "wan_ipv6",
            "2001:db8::50/48",
            "WAN IPv6 prefix length cannot "
            "be changed from this form.",
        ),
    ],
)
def test_wan_rejects_prefix_length_changes(
    isolated_wan,
    field,
    changed_address,
    expected_error,
):
    with pytest.raises(HTTPException) as exc:
        submit_wan(**{field: changed_address})

    assert exc.value.status_code == 400
    assert exc.value.detail == expected_error

    isolated_wan["netbox_patch"].assert_not_called()
    isolated_wan["validator"].assert_not_called()


# ------------------------------------------------------------
# Inventory and WAN lookup failures
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "failure_source",
    ["device_lookup", "wan_lookup"],
)
def test_wan_handles_lookup_errors(
    isolated_wan,
    failure_source,
):
    original_error = RuntimeError(
        "Simulated WAN lookup failure"
    )

    if failure_source == "device_lookup":
        isolated_wan["finder"].side_effect = original_error
    else:
        isolated_wan["wan_lookup"].side_effect = original_error

    with pytest.raises(HTTPException) as exc:
        submit_wan()

    assert exc.value.status_code == 503
    assert exc.value.detail == str(original_error)
    assert exc.value.__cause__ is original_error

    isolated_wan["netbox_patch"].assert_not_called()
    isolated_wan["validator"].assert_not_called()


# ------------------------------------------------------------
# NetBox PATCH failures
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "failed_patch_number",
    [1, 2],
)
def test_wan_handles_netbox_patch_failure(
    isolated_wan,
    failed_patch_number,
):
    """A failed write must prevent automatic validation."""

    original_error = RuntimeError(
        "Simulated WAN NetBox PATCH failure"
    )

    patch = isolated_wan["netbox_patch"]

    if failed_patch_number == 1:
        patch.side_effect = original_error
    else:
        patch.side_effect = [
            {"id": 201},
            original_error,
        ]

    with pytest.raises(HTTPException) as exc:
        submit_wan()

    assert exc.value.status_code == 503
    assert exc.value.detail == str(original_error)
    assert exc.value.__cause__ is original_error

    assert patch.call_count == failed_patch_number

    isolated_wan["validator"].assert_not_called()


# ------------------------------------------------------------
# Successful WAN update and automatic validation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "returncode, return_to, expected_path, expected_status",
    [
        (
            0,
            "/automation",
            "/automation",
            "success",
        ),
        (
            1,
            "https://untrusted.example",
            "/changes",
            "error",
        ),
    ],
)
def test_wan_update_and_validation_feedback(
    isolated_wan,
    returncode,
    return_to,
    expected_path,
    expected_status,
):
    patch = isolated_wan["netbox_patch"]
    validator = isolated_wan["validator"]

    patch.side_effect = None
    patch.return_value = {"status": "simulated"}

    validator.side_effect = None
    validator.return_value = {
        "returncode": returncode,
        "stdout": "Simulated validation",
        "stderr": "",
    }

    result = submit_wan(
        return_to=return_to
    )

    assert result.status_code == 303

    redirect = urlsplit(result.headers["location"])
    params = parse_qs(redirect.query)

    assert redirect.path == expected_path
    assert params["device"] == ["R1"]
    assert params["status"] == [expected_status]

    message = params["message"][0]

    assert "WAN addresses for R1 were updated" in message

    if returncode == 0:
        assert "Automatic validation passed" in message
    else:
        assert "No deployment was performed." in message

    assert patch.call_args_list == [
        call(
            "/api/ipam/ip-addresses/201/",
            {"address": "198.51.100.50/27"},
        ),
        call(
            "/api/ipam/ip-addresses/202/",
            {"address": "2001:db8::50/64"},
        ),
    ]

    isolated_wan["wan_lookup"].assert_called_once_with(
        isolated_wan["device"]
    )

    validator.assert_called_once_with(
        hostname="R1"
    )


# ------------------------------------------------------------
# Device metadata request helpers
# ------------------------------------------------------------

def make_metadata_request(**overrides):
    fields = {
        "hostname": "R1",
        "site_id": "10",
        "return_to": "/changes",
    }

    fields.update(overrides)

    request = Mock()
    request.form = AsyncMock(
        return_value=FormData(list(fields.items()))
    )

    return request


def submit_metadata(**overrides):
    return asyncio.run(
        cr.update_device_metadata(
            make_metadata_request(**overrides)
        )
    )


@pytest.fixture
def isolated_metadata(monkeypatch, isolated_changes):
    """Provide a mocked managed-device lookup."""

    finder = Mock(
        return_value=isolated_changes["device"]
    )

    monkeypatch.setattr(
        cr,
        "find_managed_device",
        finder,
    )

    isolated_changes["finder"] = finder

    return isolated_changes


# ------------------------------------------------------------
# Changes page without a selected device
# ------------------------------------------------------------

def test_changes_page_without_selected_device(
    isolated_changes,
):
    request = Mock()

    result = cr.changes_page(
        request=request,
        status="success",
        message="Simulated message",
    )

    renderer = isolated_changes["renderer"]

    assert result is renderer.return_value

    renderer.assert_called_once()

    context = renderer.call_args.kwargs["context"]

    assert context["selected_device"] is None
    assert context["raw_device"] is None
    assert context["compatible_profiles"] == []
    assert context["template_path"] is None
    assert context["wan_state"] is None

    assert context["action_status"] == "success"
    assert context["action_message"] == "Simulated message"

    isolated_changes["netbox_get"].assert_not_called()
    isolated_changes["netbox_patch"].assert_not_called()
    isolated_changes["validator"].assert_not_called()


# ------------------------------------------------------------
# Reject unmanaged devices
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "device",
    [
        None,
        {
            "hostname": "R5",
            "device_id": 15,
            "automation_managed": False,
        },
        {
            "hostname": "R1",
            "device_id": 11,
            "automation_managed": 0,
        },
    ],
)
def test_metadata_rejects_unmanaged_device(
    isolated_metadata,
    device,
):
    isolated_metadata["finder"].return_value = device

    with pytest.raises(HTTPException) as exc:
        submit_metadata()

    assert exc.value.status_code == 403
    assert exc.value.detail == (
        "Device is outside managed automation scope."
    )

    isolated_metadata["sites"].assert_not_called()
    isolated_metadata["netbox_patch"].assert_not_called()
    isolated_metadata["validator"].assert_not_called()


# ------------------------------------------------------------
# Invalid numeric site selection
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "site_id",
    ["", "not-a-number"],
)
def test_metadata_rejects_non_numeric_site(
    isolated_metadata,
    site_id,
):
    with pytest.raises(HTTPException) as exc:
        submit_metadata(site_id=site_id)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid site selection."

    isolated_metadata["sites"].assert_not_called()
    isolated_metadata["netbox_patch"].assert_not_called()
    isolated_metadata["validator"].assert_not_called()


# ------------------------------------------------------------
# Site does not exist in NetBox inventory
# ------------------------------------------------------------

def test_metadata_rejects_unknown_site(
    isolated_metadata,
):
    with pytest.raises(HTTPException) as exc:
        submit_metadata(site_id="999")

    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "Selected site does not exist."
    )

    isolated_metadata["sites"].assert_called_once_with()
    isolated_metadata["netbox_patch"].assert_not_called()
    isolated_metadata["validator"].assert_not_called()


# ------------------------------------------------------------
# Missing NetBox device ID
# ------------------------------------------------------------

def test_metadata_requires_netbox_device_id(
    isolated_metadata,
):
    isolated_metadata["device"]["device_id"] = None

    with pytest.raises(HTTPException) as exc:
        submit_metadata()

    assert exc.value.status_code == 500
    assert exc.value.detail == (
        "Managed device has no NetBox device ID."
    )

    isolated_metadata["netbox_patch"].assert_not_called()
    isolated_metadata["validator"].assert_not_called()


# ------------------------------------------------------------
# Inventory lookup and NetBox PATCH failures
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "failure_source",
    [
        "device_lookup",
        "site_lookup",
        "netbox_patch",
    ],
)
def test_metadata_handles_service_failure(
    isolated_metadata,
    failure_source,
):
    original_error = RuntimeError(
        f"Simulated {failure_source} failure"
    )

    if failure_source == "device_lookup":
        isolated_metadata["finder"].side_effect = (
            original_error
        )

    elif failure_source == "site_lookup":
        isolated_metadata["sites"].side_effect = (
            original_error
        )

    else:
        isolated_metadata["netbox_patch"].side_effect = (
            original_error
        )

    with pytest.raises(HTTPException) as exc:
        submit_metadata()

    assert exc.value.status_code == 503
    assert exc.value.detail == str(original_error)
    assert exc.value.__cause__ is original_error

    if failure_source == "netbox_patch":
        isolated_metadata["netbox_patch"].assert_called_once_with(
            "/api/dcim/devices/11/",
            {"site": 10},
        )
    else:
        isolated_metadata["netbox_patch"].assert_not_called()

    isolated_metadata["validator"].assert_not_called()


# ------------------------------------------------------------
# Successful metadata update and automatic validation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "returncode, return_to, expected_path, expected_status",
    [
        (0, "/changes", "/changes", "success"),
        (1, "/automation", "/automation", "error"),
        (
            1,
            "https://untrusted.example",
            "/changes",
            "error",
        ),
    ],
)
def test_metadata_update_and_validation_feedback(
    isolated_metadata,
    returncode,
    return_to,
    expected_path,
    expected_status,
):
    patch = isolated_metadata["netbox_patch"]
    validator = isolated_metadata["validator"]

    patch.side_effect = None
    patch.return_value = {
        "id": 11,
        "site": 10,
    }

    validator.side_effect = None
    validator.return_value = {
        "returncode": returncode,
        "stdout": "Simulated validation output",
        "stderr": "",
    }

    result = submit_metadata(
        site_id=" 10 ",
        return_to=return_to,
    )

    assert result.status_code == 303

    redirect = urlsplit(result.headers["location"])
    params = parse_qs(redirect.query)

    assert redirect.path == expected_path
    assert params["device"] == ["R1"]
    assert params["status"] == [expected_status]

    message = params["message"][0]

    assert "Device metadata for R1 was updated" in message

    if returncode == 0:
        assert "Automatic validation passed" in message
    else:
        assert "No deployment was performed." in message

    patch.assert_called_once_with(
        "/api/dcim/devices/11/",
        {
            "site": 10,
        },
    )

    validator.assert_called_once_with(
        hostname="R1"
    )
