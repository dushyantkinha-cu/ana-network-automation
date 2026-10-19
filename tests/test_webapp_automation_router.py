"""Isolated unit tests for the FastAPI Automation Router."""

from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

import webapp.routers.automation as ar


# ------------------------------------------------------------
# Isolated environment: block all external operations
# ------------------------------------------------------------

@pytest.fixture
def isolated_automation(monkeypatch):
    device = {
        "hostname": "R1",
        "device_id": 11,
        "platform": "Test Platform",
        "config_profile": "router",
        "automation_managed": True,
    }

    routing_choices = [
        {"value": "ospf", "label": "OSPF"},
    ]

    profile_choices = [
        {"value": "router", "label": "Router"},
        {"value": "unsupported", "label": "Unsupported"},
    ]

    report = {
        "overall_status": "pass",
        "steps": {"render": [{"returncode": 0}]},
    }

    validation = {
        "name": "validation-test.json",
        "data": report,
    }

    snapshots = [
        {"snapshot_id": "snapshot-20261015"},
        {"snapshot_id": "snapshot-20261001"},
    ]

    job = {"name": "simulated-deployment-job"}
    builds = [{"number": 18}, {"number": 11}]

    monkeypatch.setattr(
        ar, "NETBOX_URL", "https://netbox.example"
    )
    monkeypatch.setattr(ar, "ROUTING_CHOICE_SET_ID", 101)
    monkeypatch.setattr(ar, "PROFILE_CHOICE_SET_ID", 202)
    monkeypatch.setattr(
        ar,
        "PROFILE_TEMPLATE_MAP",
        {
            "Test Platform": {
                "router": "templates/test-router.j2",
            },
        },
    )

    def choice_lookup(choice_set_id):
        if choice_set_id == 101:
            return routing_choices
        if choice_set_id == 202:
            return profile_choices
        raise AssertionError("Unexpected choice-set ID")

    mocks = {
        "get_devices": Mock(return_value=[device]),
        "find_managed_device": Mock(return_value=device),
        "netbox_get": Mock(
            return_value={"id": 11, "name": "R1"}
        ),
        "get_choice_values": Mock(
            side_effect=choice_lookup
        ),
        "get_sites": Mock(
            return_value=[{"id": 10, "name": "Test Site"}]
        ),
        "get_wan_address_state": Mock(
            return_value={
                "interface_id": 44,
                "interface_name": "Ethernet1",
                "ipv4": None,
                "ipv6": None,
            }
        ),
        "get_deployment_job": Mock(return_value=job),
        "get_recent_device_builds": Mock(
            return_value=builds
        ),
        "latest_validation_report": Mock(
            return_value=validation
        ),
        "validation_step_status": Mock(
            return_value={"render": True}
        ),
        "golden_snapshots": Mock(
            return_value=snapshots
        ),
        "run_validation": Mock(
            side_effect=AssertionError(
                "Unexpected validation subprocess"
            )
        ),
        "trigger_deployment": Mock(
            side_effect=AssertionError(
                "Unexpected Jenkins deployment request"
            )
        ),
    }

    for name, mock in mocks.items():
        monkeypatch.setattr(ar, name, mock)

    renderer = Mock()

    monkeypatch.setattr(
        ar.templates, "TemplateResponse", renderer
    )

    return {
        "device": device,
        "job": job,
        "builds": builds,
        "validation": validation,
        "snapshots": snapshots,
        "routing_choices": routing_choices,
        "profile_choices": profile_choices,
        "mocks": mocks,
        "renderer": renderer,
    }


def page_context(environment, **kwargs):
    """Render a simulated page and return the template context."""

    request = Mock()

    response = ar.automation_page(
        request=request,
        **kwargs,
    )

    renderer = environment["renderer"]

    assert response is renderer.return_value
    renderer.assert_called_once()

    arguments = renderer.call_args.kwargs

    assert arguments["request"] is request
    assert arguments["name"] == "automation.html"

    return arguments["context"]


# ------------------------------------------------------------
# Page rendering without a selected device
# ------------------------------------------------------------

def test_automation_page_without_device(
    isolated_automation,
):
    env = isolated_automation
    mocks = env["mocks"]

    context = page_context(env)

    assert context["page_title"] == "Automation"
    assert context["selected_device"] is None
    assert context["raw_device"] is None
    assert context["routing_choices"] == []
    assert context["compatible_profiles"] == []
    assert context["template_path"] is None
    assert context["wan_state"] is None
    assert context["sites"] == []

    assert context["deployment_job"] is None
    assert context["deployment_device_build"] is None
    assert context["deployment_device_builds"] == []
    assert context["deployment_error"] is None

    assert context["validation"] == env["validation"]
    assert context["report"] == env["validation"]["data"]
    assert context["step_status"] == {"render": True}

    assert context["golden_snapshots"] == env["snapshots"]
    assert context["latest_golden"] == env["snapshots"][0]

    mocks["netbox_get"].assert_not_called()
    mocks["get_deployment_job"].assert_not_called()
    mocks["get_recent_device_builds"].assert_not_called()
    mocks["trigger_deployment"].assert_not_called()

    mocks["validation_step_status"].assert_called_once_with(
        env["validation"]["data"]
    )


# ------------------------------------------------------------
# Page rendering with a selected device
# ------------------------------------------------------------

def test_automation_page_selected_device(
    isolated_automation,
):
    env = isolated_automation
    mocks = env["mocks"]

    context = page_context(
        env,
        device="R1",
        status="success",
        message="Simulated message",
    )

    assert context["selected_device"] == env["device"]
    assert context["raw_device"] == {
        "id": 11,
        "name": "R1",
    }

    assert context["compatible_profiles"] == [
        {"value": "router", "label": "Router"}
    ]

    assert context["template_path"] == (
        "templates/test-router.j2"
    )

    assert context["deployment_job"] == env["job"]
    assert context["deployment_device_builds"] == env["builds"]
    assert context["deployment_device_build"] == env["builds"][0]
    assert context["deployment_error"] is None

    assert context["action_status"] == "success"
    assert context["action_message"] == "Simulated message"
    assert context["netbox_url"] == "https://netbox.example"

    mocks["netbox_get"].assert_called_once_with(
        "/api/dcim/devices/11/"
    )

    mocks["get_recent_device_builds"].assert_called_once_with(
        "R1",
        limit=5,
    )

    mocks["get_wan_address_state"].assert_called_once_with(
        env["device"]
    )

    mocks["trigger_deployment"].assert_not_called()


# ------------------------------------------------------------
# Device with no deployment history
# ------------------------------------------------------------

def test_automation_page_without_build_history(
    isolated_automation,
):
    env = isolated_automation

    env["mocks"]["get_recent_device_builds"].return_value = []

    context = page_context(env, device="R1")

    assert context["deployment_job"] == env["job"]
    assert context["deployment_device_build"] is None
    assert context["deployment_device_builds"] == []
    assert context["deployment_error"] is None


# ------------------------------------------------------------
# Jenkins unavailable: page should still load
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "failing_service",
    [
        "get_deployment_job",
        "get_recent_device_builds",
    ],
)
def test_automation_page_handles_jenkins_failure(
    isolated_automation,
    failing_service,
):
    env = isolated_automation

    env["mocks"][failing_service].side_effect = (
        RuntimeError("Simulated Jenkins outage")
    )

    context = page_context(env, device="R1")

    assert context["selected_device"] == env["device"]
    assert context["deployment_error"] == (
        "Simulated Jenkins outage"
    )
    assert context["deployment_device_build"] is None
    assert context["deployment_device_builds"] == []

    assert context["report"] == env["validation"]["data"]


# ------------------------------------------------------------
# No validation report or golden snapshots
# ------------------------------------------------------------

def test_automation_page_without_reports_or_snapshots(
    isolated_automation,
):
    env = isolated_automation
    mocks = env["mocks"]

    mocks["latest_validation_report"].return_value = None
    mocks["golden_snapshots"].return_value = []

    context = page_context(env)

    assert context["validation"] is None
    assert context["report"] is None
    assert context["golden_snapshots"] == []
    assert context["latest_golden"] is None

    mocks["validation_step_status"].assert_called_once_with(
        None
    )


# ------------------------------------------------------------
# Reject devices outside inventory
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "hostname",
    ["R5", "R999"],
)
def test_automation_page_rejects_unknown_device(
    isolated_automation,
    hostname,
):
    env = isolated_automation

    with pytest.raises(HTTPException) as exc:
        ar.automation_page(
            request=Mock(),
            device=hostname,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == (
        "Device is not in managed automation scope."
    )

    env["mocks"]["netbox_get"].assert_not_called()
    env["mocks"]["get_deployment_job"].assert_not_called()
    env["renderer"].assert_not_called()


# ------------------------------------------------------------
# Missing NetBox device ID
# ------------------------------------------------------------

def test_automation_page_requires_netbox_device_id(
    isolated_automation,
):
    env = isolated_automation
    env["device"]["device_id"] = None

    with pytest.raises(HTTPException) as exc:
        ar.automation_page(
            request=Mock(),
            device="R1",
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == (
        "Managed device has no NetBox device ID."
    )

    env["mocks"]["netbox_get"].assert_not_called()
    env["renderer"].assert_not_called()


# ------------------------------------------------------------
# Inventory service failures
# ------------------------------------------------------------

def test_automation_page_handles_inventory_failure(
    isolated_automation,
):
    env = isolated_automation

    error = RuntimeError("Simulated inventory outage")
    env["mocks"]["get_devices"].side_effect = error

    with pytest.raises(HTTPException) as exc:
        ar.automation_page(request=Mock())

    assert exc.value.status_code == 503
    assert exc.value.detail == str(error)
    assert exc.value.__cause__ is error

    env["renderer"].assert_not_called()


# ------------------------------------------------------------
# Device details and NetBox lookup failures
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "failing_service",
    [
        "netbox_get",
        "routing_choices",
        "profile_choices",
        "get_sites",
        "get_wan_address_state",
    ],
)
def test_automation_page_handles_device_detail_failure(
    isolated_automation,
    failing_service,
):
    env = isolated_automation
    mocks = env["mocks"]

    error = RuntimeError(
        "Simulated device-detail lookup failure"
    )

    if failing_service == "routing_choices":
        mocks["get_choice_values"].side_effect = error

    elif failing_service == "profile_choices":
        mocks["get_choice_values"].side_effect = [
            env["routing_choices"],
            error,
        ]

    else:
        mocks[failing_service].side_effect = error

    with pytest.raises(HTTPException) as exc:
        ar.automation_page(
            request=Mock(),
            device="R1",
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == str(error)
    assert exc.value.__cause__ is error

    env["renderer"].assert_not_called()
    mocks["get_deployment_job"].assert_not_called()
    mocks["trigger_deployment"].assert_not_called()


# ------------------------------------------------------------
# Validation results and redirects
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "device, returncode, expected_status",
    [
        (None, 0, "success"),
        ("R1", 0, "success"),
        (None, 1, "error"),
        ("R1", 1, "error"),
    ],
)
def test_run_validation_action_redirect(
    isolated_automation,
    device,
    returncode,
    expected_status,
):
    env = isolated_automation
    mocks = env["mocks"]

    validator = mocks["run_validation"]
    validator.side_effect = None
    validator.return_value = {
        "returncode": returncode,
        "stdout": "Simulated output",
        "stderr": "",
    }

    result = ar.run_validation_action(device=device)

    assert result.status_code == 303

    redirect = urlsplit(result.headers["location"])
    params = parse_qs(redirect.query)

    assert redirect.path == "/automation"
    assert params["status"] == [expected_status]

    if device is None:
        assert "device" not in params
    else:
        assert params["device"] == [device]

    message = params["message"][0]

    if returncode == 0:
        assert "Validation completed successfully" in message
    else:
        assert "Validation failed" in message

    if device:
        assert device in message
    elif returncode == 0:
        assert "All managed devices passed" in message
    else:
        assert "Review the latest validation report" in message

    validator.assert_called_once_with(hostname=device)
    mocks["trigger_deployment"].assert_not_called()


# ------------------------------------------------------------
# Deployment action: reject unsupported operations
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "action",
    ["", "restart", "delete"],
)
def test_deployment_rejects_unsupported_action(
    isolated_automation,
    action,
):
    mocks = isolated_automation["mocks"]

    with pytest.raises(HTTPException) as exc:
        ar.deployment_action(
            device="R1",
            action=action,
            confirm_device="R1",
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "Unsupported deployment action."
    )

    mocks["find_managed_device"].assert_not_called()
    mocks["trigger_deployment"].assert_not_called()


# ------------------------------------------------------------
# Apply requires exact device confirmation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "confirmation",
    ["", "r1", "R1 "],
)
def test_apply_rejects_incorrect_confirmation(
    isolated_automation,
    confirmation,
):
    mocks = isolated_automation["mocks"]

    with pytest.raises(HTTPException) as exc:
        ar.deployment_action(
            device="R1",
            action="apply",
            confirm_device=confirmation,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "Apply confirmation must exactly match "
        "the selected device."
    )

    mocks["find_managed_device"].assert_not_called()
    mocks["trigger_deployment"].assert_not_called()


# ------------------------------------------------------------
# Deployment scope enforcement
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "managed_device",
    [
        None,
        {},
        {
            "hostname": "R5",
            "automation_managed": False,
        },
        {
            "hostname": "R1",
            "automation_managed": "true",
        },
    ],
)
def test_deployment_rejects_unmanaged_device(
    isolated_automation,
    managed_device,
):
    mocks = isolated_automation["mocks"]

    mocks["find_managed_device"].return_value = (
        managed_device
    )

    with pytest.raises(HTTPException) as exc:
        ar.deployment_action(
            device="R1",
            action="dry-run",
            confirm_device="",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == (
        "Device is not in managed automation scope."
    )

    mocks["find_managed_device"].assert_called_once_with(
        "R1"
    )
    mocks["trigger_deployment"].assert_not_called()


# ------------------------------------------------------------
# Managed-device lookup failure
# ------------------------------------------------------------

def test_deployment_handles_inventory_failure(
    isolated_automation,
):
    mocks = isolated_automation["mocks"]

    original_error = RuntimeError(
        "Simulated inventory lookup failure"
    )

    mocks["find_managed_device"].side_effect = (
        original_error
    )

    with pytest.raises(HTTPException) as exc:
        ar.deployment_action(
            device="R1",
            action="preview",
            confirm_device="",
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == str(original_error)
    assert exc.value.__cause__ is original_error

    mocks["trigger_deployment"].assert_not_called()


# ------------------------------------------------------------
# Jenkins trigger failures
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "action, confirmation",
    [
        ("dry-run", ""),
        ("preview", ""),
        ("apply", "R1"),
    ],
)
def test_deployment_handles_jenkins_failure(
    isolated_automation,
    action,
    confirmation,
):
    mocks = isolated_automation["mocks"]

    original_error = RuntimeError(
        "Simulated Jenkins trigger failure"
    )

    mocks["trigger_deployment"].side_effect = (
        original_error
    )

    with pytest.raises(HTTPException) as exc:
        ar.deployment_action(
            device="R1",
            action=action,
            confirm_device=confirmation,
        )

    assert exc.value.status_code == 502
    assert exc.value.detail == str(original_error)
    assert exc.value.__cause__ is original_error

    mocks["find_managed_device"].assert_called_once_with(
        "R1"
    )

    expected_kwargs = {
        "device": "R1",
        "action": action,
    }

    if action == "apply":
        expected_kwargs["confirm_device"] = "R1"

    mocks["trigger_deployment"].assert_called_once_with(
        **expected_kwargs
    )


# ------------------------------------------------------------
# Successful request construction and redirect
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "action, confirmation, expected_label",
    [
        ("dry-run", "", "Dry run"),
        ("preview", "does-not-matter", "Preview"),
        ("apply", "R1", "Apply"),
    ],
)
def test_deployment_queues_mocked_jenkins_action(
    isolated_automation,
    action,
    confirmation,
    expected_label,
):
    """Verify requests without triggering real Jenkins builds."""

    mocks = isolated_automation["mocks"]

    trigger = mocks["trigger_deployment"]

    # Override the safety guard with a harmless simulated result.
    trigger.side_effect = None
    trigger.return_value = {
        "queued": True,
        "simulated": True,
    }

    result = ar.deployment_action(
        device="R1",
        action=action,
        confirm_device=confirmation,
    )

    assert result.status_code == 303

    redirect = urlsplit(result.headers["location"])
    params = parse_qs(redirect.query)

    assert redirect.path == "/automation"
    assert params["device"] == ["R1"]
    assert params["status"] == ["success"]

    assert params["message"] == [
        f"{expected_label} queued successfully "
        "in Jenkins for R1."
    ]

    expected_kwargs = {
        "device": "R1",
        "action": action,
    }

    if action == "apply":
        expected_kwargs["confirm_device"] = "R1"

    trigger.assert_called_once_with(
        **expected_kwargs
    )

    mocks["find_managed_device"].assert_called_once_with(
        "R1"
    )

    mocks["run_validation"].assert_not_called()
