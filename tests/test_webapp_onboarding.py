"""Isolated unit tests for the FastAPI device onboarding router."""

import asyncio
import ipaddress
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from starlette.datastructures import FormData

import webapp.routers.onboarding as ob


@pytest.fixture
def isolated_onboarding(monkeypatch):
    """Mock every service lookup and every possible NetBox write."""

    monkeypatch.setattr(ob, "ROUTING_CHOICE_SET_ID", 101)
    monkeypatch.setattr(ob, "PROFILE_CHOICE_SET_ID", 202)
    monkeypatch.setattr(
        ob,
        "MANAGEMENT_NETWORK",
        ipaddress.ip_network("172.20.20.0/24"),
    )
    monkeypatch.setattr(
        ob,
        "NETBOX_URL",
        "https://netbox.example",
    )
    monkeypatch.setattr(
        ob,
        "PROFILE_TEMPLATE_MAP",
        {"Test Platform": {"router": "templates/test-router.j2"}},
    )
    monkeypatch.setattr(
        ob,
        "PROFILE_ROLE_MAP",
        {"router": "Router"},
    )

    data = {
        "sites": [{"id": 10, "name": "Test Site"}],
        "device_types": [
            {
                "id": 20,
                "name": "Test Router",
                "manufacturer": {"id": 30, "name": "Test Vendor"},
            }
        ],
        "platforms": [
            {
                "id": 40,
                "name": "Test Platform",
                "manufacturer": {"id": 30, "name": "Test Vendor"},
            }
        ],
        "roles": [{"id": 50, "name": "Router"}],
        "routing_choices": [
            {"value": "ospf", "label": "OSPF"}
        ],
        "profile_choices": [
            {"value": "router", "label": "Router"}
        ],
        "staged_devices": [{"id": 71, "name": "TEST-STAGED"}],
    }

    readers = {
        "get_sites": Mock(return_value=data["sites"]),
        "get_device_types": Mock(return_value=data["device_types"]),
        "get_platforms": Mock(return_value=data["platforms"]),
        "get_network_roles": Mock(return_value=data["roles"]),
        "get_staged_devices": Mock(
            return_value=data["staged_devices"]
        ),
    }

    for name, reader in readers.items():
        monkeypatch.setattr(ob, name, reader)

    def choice_lookup(choice_set_id):
        if choice_set_id == 101:
            return data["routing_choices"]
        if choice_set_id == 202:
            return data["profile_choices"]
        raise AssertionError("Unexpected choice-set ID")

    readers["get_choice_values"] = Mock(
        side_effect=choice_lookup
    )
    monkeypatch.setattr(
        ob,
        "get_choice_values",
        readers["get_choice_values"],
    )

    def forbid_netbox_operation(*args, **kwargs):
        raise AssertionError(
            "Unexpected external NetBox operation in unit test"
        )

    netbox_guards = {}

    for name in (
        "netbox_get",
        "netbox_post",
        "netbox_patch",
        "netbox_delete",
    ):
        guard = Mock(side_effect=forbid_netbox_operation)
        monkeypatch.setattr(ob, name, guard)
        netbox_guards[name] = guard

    return {
        "data": data,
        "readers": readers,
        "netbox": netbox_guards,
    }


def make_onboarding_request(**overrides):
    """Create a simulated form; no HTTP server is started."""

    fields = {
        "hostname": "TEST-R1",
        "site_id": "10",
        "vendor_id": "30",
        "device_type_id": "20",
        "platform_id": "40",
        "role_id": "50",
        "management_ip": "172.20.20.50/24",
        "config_profile": "router",
    }
    fields.update(overrides)

    request = Mock()
    request.form = AsyncMock(
        return_value=FormData(list(fields.items()))
    )
    return request


def submit_onboarding(**overrides):
    """Invoke the asynchronous router directly with mocked input."""

    request = make_onboarding_request(**overrides)
    return asyncio.run(ob.create_staged_device(request))


# ------------------------------------------------------------
# GET /changes/new — Page rendering
# ------------------------------------------------------------

def test_new_device_page_builds_expected_context(
    monkeypatch,
    isolated_onboarding,
):
    readers = isolated_onboarding["readers"]

    readers["get_device_types"].return_value = [
        {
            "id": 20,
            "manufacturer": {"id": 30, "name": "Zulu Vendor"},
        },
        {
            "id": 21,
            "manufacturer": None,
        },
    ]

    readers["get_platforms"].return_value = [
        {
            "id": 40,
            "manufacturer": {"id": 31, "name": "Alpha Vendor"},
        },
        {
            "id": 41,
            "manufacturer": {"id": 30, "name": "Zulu Vendor"},
        },
    ]

    renderer = Mock()
    monkeypatch.setattr(
        ob.templates,
        "TemplateResponse",
        renderer,
    )

    request = Mock()

    result = ob.new_device_page(
        request=request,
        status="success",
        message="Simulated message",
    )

    assert result is renderer.return_value

    kwargs = renderer.call_args.kwargs
    context = kwargs["context"]

    assert kwargs["request"] is request
    assert kwargs["name"] == "new_device.html"

    assert context["page_title"] == "Add Device"
    assert context["sites"] == isolated_onboarding["data"]["sites"]

    assert context["vendors"] == [
        {"id": 31, "name": "Alpha Vendor"},
        {"id": 30, "name": "Zulu Vendor"},
    ]

    assert context["roles"] == isolated_onboarding["data"]["roles"]
    assert context["routing_choices"] == (
        isolated_onboarding["data"]["routing_choices"]
    )
    assert context["profile_choices"] == (
        isolated_onboarding["data"]["profile_choices"]
    )
    assert context["staged_devices"] == (
        isolated_onboarding["data"]["staged_devices"]
    )
    assert context["action_status"] == "success"
    assert context["action_message"] == "Simulated message"
    assert context["netbox_url"] == "https://netbox.example"

    isolated_onboarding["netbox"]["netbox_post"].assert_not_called()


# ------------------------------------------------------------
# GET /changes/new — Service failures
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "service_name",
    [
        "get_sites",
        "get_device_types",
        "get_platforms",
        "get_network_roles",
        "get_staged_devices",
    ],
)
def test_new_device_page_service_failure(
    monkeypatch,
    isolated_onboarding,
    service_name,
):
    error = RuntimeError("Simulated inventory service outage")

    isolated_onboarding["readers"][service_name].side_effect = error

    renderer = Mock()
    monkeypatch.setattr(
        ob.templates,
        "TemplateResponse",
        renderer,
    )

    with pytest.raises(HTTPException) as exc:
        ob.new_device_page(request=Mock())

    assert exc.value.status_code == 503
    assert exc.value.detail == "Simulated inventory service outage"
    assert exc.value.__cause__ is error

    renderer.assert_not_called()


@pytest.mark.parametrize(
    "failure_call",
    [1, 2],
)
def test_new_device_page_choice_set_failure(
    monkeypatch,
    isolated_onboarding,
    failure_call,
):
    error = RuntimeError("Simulated choice-set failure")
    choice_reader = isolated_onboarding["readers"]["get_choice_values"]

    if failure_call == 1:
        choice_reader.side_effect = error
    else:
        choice_reader.side_effect = [
            isolated_onboarding["data"]["routing_choices"],
            error,
        ]

    renderer = Mock()
    monkeypatch.setattr(
        ob.templates,
        "TemplateResponse",
        renderer,
    )

    with pytest.raises(HTTPException) as exc:
        ob.new_device_page(request=Mock())

    assert exc.value.status_code == 503
    assert exc.value.detail == "Simulated choice-set failure"

    renderer.assert_not_called()


# ------------------------------------------------------------
# POST /changes/new — Hostname validation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "hostname",
    [
        "",
        "bad hostname",
        "-R1",
        ".R1",
        "R1!",
        "R" * 65,
    ],
)
def test_create_staged_device_rejects_invalid_hostname(
    isolated_onboarding,
    hostname,
):
    with pytest.raises(HTTPException) as exc:
        submit_onboarding(hostname=hostname)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid device hostname."

    isolated_onboarding["readers"]["get_sites"].assert_not_called()

    for guard in isolated_onboarding["netbox"].values():
        guard.assert_not_called()


# ------------------------------------------------------------
# POST /changes/new — Invalid NetBox ID selections
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "field_name",
    [
        "site_id",
        "vendor_id",
        "device_type_id",
        "platform_id",
        "role_id",
    ],
)
def test_create_staged_device_rejects_non_numeric_selection(
    isolated_onboarding,
    field_name,
):
    with pytest.raises(HTTPException) as exc:
        submit_onboarding(**{field_name: "not-a-number"})

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid NetBox object selection."

    isolated_onboarding["readers"]["get_sites"].assert_not_called()

    for guard in isolated_onboarding["netbox"].values():
        guard.assert_not_called()


# ------------------------------------------------------------
# Valid numeric IDs that do not exist in inventory
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "field_name, expected_error",
    [
        ("site_id", "Invalid site selection."),
        (
            "device_type_id",
            "Invalid device type, platform, or role.",
        ),
        (
            "platform_id",
            "Invalid device type, platform, or role.",
        ),
        (
            "role_id",
            "Invalid device type, platform, or role.",
        ),
    ],
)
def test_onboarding_rejects_unknown_inventory_selection(
    isolated_onboarding,
    field_name,
    expected_error,
):
    with pytest.raises(HTTPException) as exc:
        submit_onboarding(**{field_name: "999"})

    assert exc.value.status_code == 400
    assert exc.value.detail == expected_error

    for guard in isolated_onboarding["netbox"].values():
        guard.assert_not_called()


# ------------------------------------------------------------
# Device type and platform manufacturer compatibility
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "inventory_field",
    ["device_types", "platforms"],
)
def test_onboarding_rejects_manufacturer_mismatch(
    isolated_onboarding,
    inventory_field,
):
    """Both device type and platform must belong to the chosen vendor."""

    isolated_onboarding["data"][inventory_field][0][
        "manufacturer"
    ]["id"] = 999

    with pytest.raises(HTTPException) as exc:
        submit_onboarding()

    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "Vendor, device type, and platform are not compatible."
    )

    for guard in isolated_onboarding["netbox"].values():
        guard.assert_not_called()


# ------------------------------------------------------------
# Configuration profile compatibility
# ------------------------------------------------------------

def test_onboarding_rejects_unsupported_profile(
    isolated_onboarding,
):
    with pytest.raises(HTTPException) as exc:
        submit_onboarding(
            config_profile="unsupported-profile",
        )

    assert exc.value.status_code == 400
    assert "Configuration profile is not supported" in (
        exc.value.detail
    )

    for guard in isolated_onboarding["netbox"].values():
        guard.assert_not_called()


def test_onboarding_rejects_incompatible_device_role(
    isolated_onboarding,
):
    """A router profile cannot be assigned an incompatible role."""

    isolated_onboarding["data"]["roles"][0]["name"] = (
        "Multilayer Switch"
    )

    with pytest.raises(HTTPException) as exc:
        submit_onboarding()

    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "Profile 'router' requires role 'Router'."
    )

    for guard in isolated_onboarding["netbox"].values():
        guard.assert_not_called()


# ------------------------------------------------------------
# Routing protocol selection
# ------------------------------------------------------------

def test_onboarding_rejects_unapproved_routing_protocol(
    isolated_onboarding,
):
    """Routing protocols must come from the allowed NetBox choice set."""

    with pytest.raises(HTTPException) as exc:
        submit_onboarding(
            routing_protocols="unsupported-protocol",
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "Invalid routing protocol selection."
    )

    for guard in isolated_onboarding["netbox"].values():
        guard.assert_not_called()


# ------------------------------------------------------------
# Management IP address validation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "management_ip, expected_error",
    [
        (
            "not-an-ip-address",
            "Management IP must use valid CIDR notation.",
        ),
        (
            "2001:db8::50/64",
            "Management address must be IPv4.",
        ),
        (
            "172.20.20.50/25",
            "Management prefix length must be /24.",
        ),
        (
            "192.0.2.50/24",
            "Management IP must be inside 172.20.20.0/24.",
        ),
    ],
)
def test_onboarding_rejects_invalid_management_address(
    isolated_onboarding,
    management_ip,
    expected_error,
):
    with pytest.raises(HTTPException) as exc:
        submit_onboarding(
            management_ip=management_ip,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == expected_error

    for guard in isolated_onboarding["netbox"].values():
        guard.assert_not_called()


# ------------------------------------------------------------
# Duplicate hostname protection
# ------------------------------------------------------------

def test_onboarding_rejects_existing_hostname(
    monkeypatch,
    isolated_onboarding,
):
    netbox = Mock(
        return_value={
            "results": [
                {"id": 999, "name": "TEST-R1"},
            ]
        }
    )

    monkeypatch.setattr(ob, "netbox_get", netbox)

    with pytest.raises(HTTPException) as exc:
        submit_onboarding()

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "A NetBox device with this hostname already exists."
    )

    netbox.assert_called_once_with(
        "/api/dcim/devices/?name=TEST-R1"
    )

    for name in ("netbox_post", "netbox_patch", "netbox_delete"):
        isolated_onboarding["netbox"][name].assert_not_called()


# ------------------------------------------------------------
# Duplicate management IP protection
# ------------------------------------------------------------

def test_onboarding_rejects_existing_management_ip(
    monkeypatch,
    isolated_onboarding,
):
    netbox = Mock(
        side_effect=[
            {"results": []},
            {
                "results": [
                    {
                        "id": 999,
                        "address": "172.20.20.50/24",
                    }
                ],
            },
        ]
    )

    monkeypatch.setattr(ob, "netbox_get", netbox)

    with pytest.raises(HTTPException) as exc:
        submit_onboarding()

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "Management IP already exists in NetBox."
    )

    assert [
        entry.args[0]
        for entry in netbox.call_args_list
    ] == [
        "/api/dcim/devices/?name=TEST-R1",
        "/api/ipam/ip-addresses/?address=172.20.20.50/24",
    ]

    for name in ("netbox_post", "netbox_patch", "netbox_delete"):
        isolated_onboarding["netbox"][name].assert_not_called()


# ------------------------------------------------------------
# NetBox read failure handling
# ------------------------------------------------------------

def test_onboarding_handles_netbox_lookup_failure(
    monkeypatch,
    isolated_onboarding,
):
    original_error = RuntimeError(
        "Simulated NetBox lookup failure"
    )

    netbox = Mock(side_effect=original_error)

    monkeypatch.setattr(ob, "netbox_get", netbox)

    with pytest.raises(HTTPException) as exc:
        submit_onboarding()

    assert exc.value.status_code == 503
    assert exc.value.detail == (
        "Simulated NetBox lookup failure"
    )
    assert exc.value.__cause__ is original_error

    netbox.assert_called_once()

    for name in ("netbox_post", "netbox_patch", "netbox_delete"):
        isolated_onboarding["netbox"][name].assert_not_called()


# ------------------------------------------------------------
# Fully mocked NetBox creation environment
# ------------------------------------------------------------

from unittest.mock import call
from urllib.parse import parse_qs, urlsplit


@pytest.fixture
def simulated_onboarding_creation(
    monkeypatch,
    isolated_onboarding,
):
    """Simulate every NetBox read and write operation."""

    lookups = Mock(
        side_effect=[
            {"results": []},
            {"results": []},
        ]
    )

    posts = Mock(
        side_effect=[
            {"id": 101},
            {"id": 201},
            {"id": 301},
        ]
    )

    patch = Mock(return_value={"id": 101})
    delete = Mock(return_value=None)

    monkeypatch.setattr(ob, "netbox_get", lookups)
    monkeypatch.setattr(ob, "netbox_post", posts)
    monkeypatch.setattr(ob, "netbox_patch", patch)
    monkeypatch.setattr(ob, "netbox_delete", delete)

    return {
        "lookups": lookups,
        "posts": posts,
        "patch": patch,
        "delete": delete,
    }


# ------------------------------------------------------------
# Successful staged-device onboarding
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "routing_protocol",
    [None, "ospf"],
)
def test_onboarding_creates_staged_device_successfully(
    simulated_onboarding_creation,
    routing_protocol,
):
    """Verify complete onboarding without making real NetBox changes."""

    mocks = simulated_onboarding_creation

    form_values = {}

    if routing_protocol is not None:
        form_values["routing_protocols"] = routing_protocol

    result = submit_onboarding(**form_values)

    assert result.status_code == 303

    redirect = urlsplit(result.headers["location"])
    params = parse_qs(redirect.query)

    assert redirect.path == "/changes/new"
    assert params["status"] == ["success"]

    message = params["message"][0]

    assert "TEST-R1 was created in NetBox" in message
    assert "templates/test-router.j2" in message
    assert "Automation remains disabled." in message

    expected_protocols = (
        [] if routing_protocol is None else [routing_protocol]
    )

    assert mocks["posts"].call_args_list == [
        call(
            "/api/dcim/devices/",
            {
                "name": "TEST-R1",
                "device_type": 20,
                "role": 50,
                "site": 10,
                "status": "staged",
                "platform": 40,
                "custom_fields": {
                    "automation_managed": False,
                    "routing_protocols": expected_protocols,
                    "config_profile": "router",
                },
            },
        ),
        call(
            "/api/dcim/interfaces/",
            {
                "device": 101,
                "name": "automation-mgmt",
                "type": "virtual",
                "enabled": True,
                "description": (
                    "Logical NMAS-reachable management endpoint; "
                    "not necessarily the device-native interface."
                ),
            },
        ),
        call(
            "/api/ipam/ip-addresses/",
            {
                "address": "172.20.20.50/24",
                "status": "active",
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": 201,
                "description": (
                    "ANA automation and management endpoint "
                    "for TEST-R1"
                ),
            },
        ),
    ]

    mocks["patch"].assert_called_once_with(
        "/api/dcim/devices/101/",
        {"primary_ip4": 301},
    )

    assert mocks["lookups"].call_args_list == [
        call("/api/dcim/devices/?name=TEST-R1"),
        call(
            "/api/ipam/ip-addresses/"
            "?address=172.20.20.50/24"
        ),
    ]

    mocks["delete"].assert_not_called()


# ------------------------------------------------------------
# Rollback after failures at each creation stage
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "failed_stage, expected_cleanup, expected_post_count",
    [
        (
            "device",
            [],
            1,
        ),
        (
            "interface",
            [
                "/api/dcim/devices/101/",
            ],
            2,
        ),
        (
            "ip",
            [
                "/api/dcim/interfaces/201/",
                "/api/dcim/devices/101/",
            ],
            3,
        ),
        (
            "primary_ip",
            [
                "/api/ipam/ip-addresses/301/",
                "/api/dcim/interfaces/201/",
                "/api/dcim/devices/101/",
            ],
            3,
        ),
    ],
)
def test_onboarding_rolls_back_partial_creation(
    simulated_onboarding_creation,
    failed_stage,
    expected_cleanup,
    expected_post_count,
):
    """All previously created NetBox objects must be cleaned up."""

    mocks = simulated_onboarding_creation

    failure = RuntimeError(
        f"Simulated {failed_stage} creation failure"
    )

    post_results = [
        {"id": 101},
        {"id": 201},
        {"id": 301},
    ]

    if failed_stage == "device":
        post_results[0] = failure

    elif failed_stage == "interface":
        post_results[1] = failure

    elif failed_stage == "ip":
        post_results[2] = failure

    else:
        mocks["patch"].side_effect = failure

    mocks["posts"].side_effect = post_results

    with pytest.raises(HTTPException) as exc:
        submit_onboarding()

    assert exc.value.status_code == 503

    assert exc.value.detail == (
        f"Simulated {failed_stage} creation failure "
        "Partial onboarding was rolled back."
    )

    assert mocks["posts"].call_count == expected_post_count

    if failed_stage == "primary_ip":
        mocks["patch"].assert_called_once()
    else:
        mocks["patch"].assert_not_called()

    assert mocks["delete"].call_args_list == [
        call(path)
        for path in expected_cleanup
    ]


# ------------------------------------------------------------
# Rollback when cleanup itself fails
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "failed_cleanup_path",
    [
        "/api/ipam/ip-addresses/301/",
        "/api/dcim/interfaces/201/",
        "/api/dcim/devices/101/",
    ],
)
def test_onboarding_reports_incomplete_rollback(
    simulated_onboarding_creation,
    failed_cleanup_path,
):
    """A failed cleanup must be reported without skipping later steps."""

    mocks = simulated_onboarding_creation

    original_failure = RuntimeError(
        "Simulated primary-IP assignment failure"
    )

    mocks["patch"].side_effect = original_failure

    def simulate_cleanup(path):
        if path == failed_cleanup_path:
            raise RuntimeError(
                f"Simulated cleanup failure: {path}"
            )

    mocks["delete"].side_effect = simulate_cleanup

    with pytest.raises(HTTPException) as exc:
        submit_onboarding()

    assert exc.value.status_code == 503

    assert "Simulated primary-IP assignment failure" in (
        exc.value.detail
    )

    assert "Rollback was incomplete:" in (
        exc.value.detail
    )

    assert f"Simulated cleanup failure: {failed_cleanup_path}" in (
        exc.value.detail
    )

    # Cleanup must continue in reverse creation order.
    assert mocks["delete"].call_args_list == [
        call("/api/ipam/ip-addresses/301/"),
        call("/api/dcim/interfaces/201/"),
        call("/api/dcim/devices/101/"),
    ]

    assert exc.value.__cause__.__cause__ is original_failure


# ------------------------------------------------------------
# Multiple failures during rollback
# ------------------------------------------------------------

def test_onboarding_reports_all_cleanup_failures(
    simulated_onboarding_creation,
):
    """Collect all rollback errors instead of hiding later failures."""

    mocks = simulated_onboarding_creation

    mocks["patch"].side_effect = RuntimeError(
        "Simulated primary-IP assignment failure"
    )

    def fail_every_cleanup(path):
        raise RuntimeError(
            f"Cleanup failed for {path}"
        )

    mocks["delete"].side_effect = fail_every_cleanup

    with pytest.raises(HTTPException) as exc:
        submit_onboarding()

    assert exc.value.status_code == 503
    assert "Rollback was incomplete:" in exc.value.detail

    for path in (
        "/api/ipam/ip-addresses/301/",
        "/api/dcim/interfaces/201/",
        "/api/dcim/devices/101/",
    ):
        assert f"Cleanup failed for {path}" in exc.value.detail

    assert mocks["delete"].call_count == 3
