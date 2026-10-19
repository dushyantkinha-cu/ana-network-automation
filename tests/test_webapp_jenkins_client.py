"""Isolated unit tests for webapp.clients.jenkins."""

from unittest.mock import Mock

import pytest
import requests

from webapp.clients import jenkins as jc


# ------------------------------------------------------------
# Simulated Jenkins configuration
# ------------------------------------------------------------

@pytest.fixture
def fake_jenkins_config(monkeypatch):
    """Replace Jenkins configuration with harmless test values."""

    monkeypatch.setattr(
        jc, "JENKINS_URL", "https://jenkins.example"
    )
    monkeypatch.setattr(
        jc, "JENKINS_USER", "unit-test-user"
    )
    monkeypatch.setattr(
        jc, "JENKINS_API_TOKEN", "unit-test-token"
    )
    monkeypatch.setattr(
        jc, "JENKINS_DEPLOY_JOB", "deploy job"
    )


# ------------------------------------------------------------
# Required Jenkins configuration
# ------------------------------------------------------------

def test_require_config_success(fake_jenkins_config):
    assert jc._require_config() is None


@pytest.mark.parametrize(
    "missing_fields",
    [
        ("JENKINS_URL",),
        ("JENKINS_USER",),
        ("JENKINS_API_TOKEN",),
        ("JENKINS_DEPLOY_JOB",),
        (
            "JENKINS_URL",
            "JENKINS_USER",
            "JENKINS_API_TOKEN",
            "JENKINS_DEPLOY_JOB",
        ),
    ],
)
def test_require_config_missing(
    monkeypatch,
    fake_jenkins_config,
    missing_fields,
):
    """Missing credentials or settings must block Jenkins requests."""

    for field in missing_fields:
        monkeypatch.setattr(jc, field, "")

    with pytest.raises(RuntimeError) as exc:
        jc._require_config()

    assert str(exc.value) == (
        "Missing Jenkins configuration: "
        + ", ".join(missing_fields)
    )


# ------------------------------------------------------------
# Jenkins job URL encoding
# ------------------------------------------------------------

def test_job_path_encodes_special_characters(
    monkeypatch,
    fake_jenkins_config,
):
    monkeypatch.setattr(
        jc,
        "JENKINS_DEPLOY_JOB",
        "folder/Deploy Pipeline",
    )

    assert jc._job_path() == (
        "folder%2FDeploy%20Pipeline"
    )


# ------------------------------------------------------------
# Successful Jenkins API request
# ------------------------------------------------------------

def test_jenkins_request_success(
    monkeypatch,
    fake_jenkins_config,
):
    """Requests must use authenticated, bounded HTTP calls."""

    response = Mock()
    response.status_code = 200

    request = Mock(return_value=response)

    monkeypatch.setattr(
        jc.requests,
        "request",
        request,
    )

    result = jc.jenkins_request(
        "GET",
        "/job/test/api/json",
        headers={"X-Test": "yes"},
    )

    assert result is response

    request.assert_called_once_with(
        "GET",
        "https://jenkins.example/job/test/api/json",
        auth=("unit-test-user", "unit-test-token"),
        timeout=jc.DEFAULT_TIMEOUT,
        headers={"X-Test": "yes"},
    )


# ------------------------------------------------------------
# Missing configuration blocks HTTP requests
# ------------------------------------------------------------

def test_jenkins_request_rejects_missing_config(
    monkeypatch,
    fake_jenkins_config,
):
    request = Mock()

    monkeypatch.setattr(
        jc.requests, "request", request
    )
    monkeypatch.setattr(
        jc, "JENKINS_API_TOKEN", ""
    )

    with pytest.raises(
        RuntimeError,
        match="Missing Jenkins configuration",
    ):
        jc.jenkins_request(
            "GET",
            "/api/json",
        )

    request.assert_not_called()


# ------------------------------------------------------------
# Network failures
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "error_type",
    [
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
    ],
)
def test_jenkins_request_network_failure(
    monkeypatch,
    fake_jenkins_config,
    error_type,
):
    original_error = error_type(
        "Simulated Jenkins network failure"
    )

    request = Mock(side_effect=original_error)

    monkeypatch.setattr(
        jc.requests,
        "request",
        request,
    )

    with pytest.raises(
        RuntimeError,
        match="Unable to reach Jenkins",
    ) as exc:
        jc.jenkins_request(
            "GET",
            "/api/json",
        )

    assert exc.value.__cause__ is original_error

    request.assert_called_once()


# ------------------------------------------------------------
# HTTP error responses
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "status_code",
    [400, 403, 500],
)
def test_jenkins_request_http_error(
    monkeypatch,
    fake_jenkins_config,
    status_code,
):
    response = Mock()
    response.status_code = status_code

    request = Mock(return_value=response)

    monkeypatch.setattr(
        jc.requests,
        "request",
        request,
    )

    with pytest.raises(RuntimeError) as exc:
        jc.jenkins_request(
            "GET",
            "/api/json",
        )

    assert str(exc.value) == (
        "Jenkins API request failed with "
        f"HTTP {status_code}."
    )

    request.assert_called_once()


# ------------------------------------------------------------
# Deployment job information
# ------------------------------------------------------------

def test_get_deployment_job_success(
    monkeypatch,
    fake_jenkins_config,
):
    expected = {
        "name": "deploy job",
        "buildable": True,
        "color": "blue",
        "lastBuild": {"number": 10},
    }

    response = Mock()
    response.json.return_value = expected

    request = Mock(return_value=response)

    monkeypatch.setattr(
        jc, "jenkins_request", request
    )

    assert jc.get_deployment_job() == expected

    request.assert_called_once_with(
        "GET",
        "/job/deploy%20job/api/json"
        "?tree=name,buildable,color,"
        "lastBuild[number,url],"
        "lastCompletedBuild[number,url,result]",
    )


def test_get_deployment_job_invalid_json(
    monkeypatch,
    fake_jenkins_config,
):
    response = Mock()
    response.json.side_effect = ValueError(
        "Simulated invalid JSON"
    )

    request = Mock(return_value=response)

    monkeypatch.setattr(
        jc, "jenkins_request", request
    )

    with pytest.raises(
        RuntimeError,
        match="Jenkins returned invalid JSON",
    ) as exc:
        jc.get_deployment_job()

    assert isinstance(exc.value.__cause__, ValueError)

    request.assert_called_once()


# ------------------------------------------------------------
# Jenkins CSRF crumb retrieval
# ------------------------------------------------------------

def test_get_crumb_success(monkeypatch):
    response = Mock()
    response.json.return_value = {
        "crumbRequestField": "Jenkins-Crumb",
        "crumb": "simulated-crumb-value",
    }

    request = Mock(return_value=response)

    monkeypatch.setattr(
        jc, "jenkins_request", request
    )

    assert jc.get_crumb() == {
        "field": "Jenkins-Crumb",
        "value": "simulated-crumb-value",
    }

    request.assert_called_once_with(
        "GET",
        "/crumbIssuer/api/json",
    )


def test_get_crumb_invalid_json(monkeypatch):
    response = Mock()
    response.json.side_effect = ValueError(
        "Simulated invalid crumb JSON"
    )

    request = Mock(return_value=response)

    monkeypatch.setattr(
        jc, "jenkins_request", request
    )

    with pytest.raises(
        RuntimeError,
        match="Jenkins returned invalid crumb JSON",
    ) as exc:
        jc.get_crumb()

    assert isinstance(exc.value.__cause__, ValueError)

    request.assert_called_once()


@pytest.mark.parametrize(
    "crumb_data",
    [
        {},
        {"crumbRequestField": "Jenkins-Crumb"},
        {"crumb": "simulated-crumb"},
        {
            "crumbRequestField": "",
            "crumb": "simulated-crumb",
        },
    ],
)
def test_get_crumb_rejects_incomplete_response(
    monkeypatch,
    crumb_data,
):
    """Missing CSRF fields must block write operations."""

    response = Mock()
    response.json.return_value = crumb_data

    request = Mock(return_value=response)

    monkeypatch.setattr(
        jc, "jenkins_request", request
    )

    with pytest.raises(
        RuntimeError,
        match="Jenkins returned an incomplete CSRF crumb",
    ):
        jc.get_crumb()

    request.assert_called_once()


# ------------------------------------------------------------
# Deployment safety: unmanaged devices
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "device",
    ["R5", "S5", "r1", ""],
)
def test_trigger_deployment_rejects_unmanaged_device(
    monkeypatch,
    device,
):
    crumb = Mock()
    request = Mock()

    monkeypatch.setattr(jc, "get_crumb", crumb)
    monkeypatch.setattr(jc, "jenkins_request", request)

    with pytest.raises(
        RuntimeError,
        match="Device is not allowed for deployment",
    ):
        jc.trigger_deployment(device, "dry-run")

    crumb.assert_not_called()
    request.assert_not_called()


# ------------------------------------------------------------
# Deployment safety: unsupported actions
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "action",
    ["destroy", "rollback", "apply-now", ""],
)
def test_trigger_deployment_rejects_invalid_action(
    monkeypatch,
    action,
):
    crumb = Mock()
    request = Mock()

    monkeypatch.setattr(jc, "get_crumb", crumb)
    monkeypatch.setattr(jc, "jenkins_request", request)

    with pytest.raises(
        RuntimeError,
        match="Unsupported deployment action",
    ):
        jc.trigger_deployment("R1", action)

    crumb.assert_not_called()
    request.assert_not_called()


# ------------------------------------------------------------
# Deployment safety: apply confirmation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "confirmation",
    ["", "R2", "r1"],
)
def test_trigger_deployment_requires_exact_apply_confirmation(
    monkeypatch,
    confirmation,
):
    crumb = Mock()
    request = Mock()

    monkeypatch.setattr(jc, "get_crumb", crumb)
    monkeypatch.setattr(jc, "jenkins_request", request)

    with pytest.raises(
        RuntimeError,
        match="Apply confirmation must exactly match",
    ):
        jc.trigger_deployment(
            "R1",
            "apply",
            confirm_device=confirmation,
        )

    crumb.assert_not_called()
    request.assert_not_called()


# ------------------------------------------------------------
# Successful deployment request construction
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "device, action, confirmation",
    [
        ("R1", "dry-run", ""),
        ("S4", "preview", ""),
        ("R2", "apply", "R2"),
    ],
)
def test_trigger_deployment_constructs_expected_request(
    monkeypatch,
    fake_jenkins_config,
    device,
    action,
    confirmation,
):
    """Verify request construction without contacting Jenkins."""

    crumb = Mock(
        return_value={
            "field": "Jenkins-Crumb",
            "value": "simulated-crumb",
        }
    )

    response = Mock()
    response.status_code = 201
    response.headers = {
        "Location": (
            "https://jenkins.example/queue/item/42/"
        )
    }

    request = Mock(return_value=response)

    monkeypatch.setattr(jc, "get_crumb", crumb)
    monkeypatch.setattr(jc, "jenkins_request", request)

    result = jc.trigger_deployment(
        device,
        action,
        confirm_device=confirmation,
    )

    crumb.assert_called_once_with()

    request.assert_called_once_with(
        "POST",
        "/job/deploy%20job/buildWithParameters",
        headers={
            "Jenkins-Crumb": "simulated-crumb",
        },
        data={
            "DEVICE": device,
            "ACTION": action,
            "CONFIRM_DEVICE": confirmation,
        },
        allow_redirects=False,
    )

    assert result == {
        "status_code": 201,
        "queue_url": (
            "https://jenkins.example/queue/item/42/"
        ),
        "device": device,
        "action": action,
    }


# ------------------------------------------------------------
# Missing Jenkins queue location
# ------------------------------------------------------------

def test_trigger_deployment_rejects_missing_queue_location(
    monkeypatch,
    fake_jenkins_config,
):
    response = Mock()
    response.status_code = 201
    response.headers = {}

    request = Mock(return_value=response)

    monkeypatch.setattr(
        jc,
        "get_crumb",
        Mock(
            return_value={
                "field": "Jenkins-Crumb",
                "value": "simulated-crumb",
            }
        ),
    )

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        request,
    )

    with pytest.raises(
        RuntimeError,
        match="did not return a queue location",
    ):
        jc.trigger_deployment("R1", "dry-run")

    request.assert_called_once()


# ------------------------------------------------------------
# Queue resolution
# ------------------------------------------------------------

import time


def test_resolve_queue_build_success(monkeypatch):
    """The queue can be polled until a build number appears."""

    waiting = Mock()
    waiting.json.return_value = {
        "executable": None,
    }

    ready = Mock()
    ready.json.return_value = {
        "executable": {
            "number": 0,
        }
    }

    request = Mock(
        side_effect=[waiting, ready]
    )

    sleeper = Mock()

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        request,
    )
    monkeypatch.setattr(time, "sleep", sleeper)

    result = jc.resolve_queue_build(
        "https://jenkins.example/queue/item/42/",
        attempts=3,
        delay=0.25,
    )

    assert result == 0

    assert request.call_count == 2

    request.assert_any_call(
        "GET",
        "/queue/item/42/api/json",
    )

    sleeper.assert_called_once_with(0.25)


def test_resolve_queue_build_cancelled(monkeypatch):
    response = Mock()
    response.json.return_value = {
        "cancelled": True,
    }

    request = Mock(return_value=response)
    sleeper = Mock()

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        request,
    )
    monkeypatch.setattr(time, "sleep", sleeper)

    with pytest.raises(
        RuntimeError,
        match="Jenkins queue item was cancelled",
    ):
        jc.resolve_queue_build(
            "https://jenkins.example/queue/item/42/",
            attempts=3,
        )

    request.assert_called_once()
    sleeper.assert_not_called()


def test_resolve_queue_build_invalid_json(monkeypatch):
    response = Mock()
    response.json.side_effect = ValueError(
        "Simulated invalid queue JSON"
    )

    request = Mock(return_value=response)
    sleeper = Mock()

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        request,
    )
    monkeypatch.setattr(time, "sleep", sleeper)

    with pytest.raises(
        RuntimeError,
        match="Jenkins returned invalid queue JSON",
    ) as exc:
        jc.resolve_queue_build(
            "https://jenkins.example/queue/item/42/",
            attempts=3,
        )

    assert isinstance(exc.value.__cause__, ValueError)

    request.assert_called_once()
    sleeper.assert_not_called()


def test_resolve_queue_build_timeout(monkeypatch):
    """A queue that never starts must eventually raise an error."""

    response = Mock()
    response.json.return_value = {
        "executable": None,
        "cancelled": False,
    }

    request = Mock(return_value=response)
    sleeper = Mock()

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        request,
    )
    monkeypatch.setattr(time, "sleep", sleeper)

    with pytest.raises(
        RuntimeError,
        match="Jenkins build did not leave the queue",
    ):
        jc.resolve_queue_build(
            "https://jenkins.example/queue/item/42/",
            attempts=2,
            delay=0.1,
        )

    assert request.call_count == 2
    assert sleeper.call_count == 2


# ------------------------------------------------------------
# Individual Jenkins build information
# ------------------------------------------------------------

def test_get_deployment_build_extracts_parameters(
    monkeypatch,
    fake_jenkins_config,
):
    """Build parameters must be extracted from Jenkins actions."""

    build_data = {
        "number": 17,
        "result": "SUCCESS",
        "building": False,
        "actions": [
            {
                "parameters": [
                    {"name": "DEVICE", "value": "R1"},
                    {"name": "ACTION", "value": "dry-run"},
                    {"value": "ignored"},
                ]
            },
            {
                "parameters": [
                    {"name": "CONFIRM_DEVICE", "value": ""},
                ]
            },
            {},
        ],
    }

    response = Mock()
    response.json.return_value = build_data

    request = Mock(return_value=response)

    monkeypatch.setattr(
        jc, "jenkins_request", request
    )

    result = jc.get_deployment_build("17")

    assert result["number"] == 17
    assert result["result"] == "SUCCESS"

    assert result["parameters"] == {
        "DEVICE": "R1",
        "ACTION": "dry-run",
        "CONFIRM_DEVICE": "",
    }

    request.assert_called_once_with(
        "GET",
        "/job/deploy%20job/17/api/json"
        "?tree=number,url,building,result,"
        "actions[parameters[name,value]]",
    )


@pytest.mark.parametrize(
    "build_data",
    [
        {},
        {
            "number": 20,
            "actions": [
                {},
                {
                    "parameters": [
                        {"value": "missing-name"},
                    ]
                },
            ],
        },
    ],
)
def test_get_deployment_build_without_parameters(
    monkeypatch,
    fake_jenkins_config,
    build_data,
):
    """Builds without named parameters must return an empty mapping."""

    response = Mock()
    response.json.return_value = build_data

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        Mock(return_value=response),
    )

    result = jc.get_deployment_build(20)

    assert result["parameters"] == {}


def test_get_deployment_build_invalid_json(
    monkeypatch,
    fake_jenkins_config,
):
    original_error = ValueError(
        "Simulated invalid build JSON"
    )

    response = Mock()
    response.json.side_effect = original_error

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        Mock(return_value=response),
    )

    with pytest.raises(
        RuntimeError,
        match="Jenkins returned invalid build JSON",
    ) as exc:
        jc.get_deployment_build(17)

    assert exc.value.__cause__ is original_error


def test_get_deployment_build_invalid_number(
    monkeypatch,
    fake_jenkins_config,
):
    """Non-numeric build identifiers must be rejected."""

    request = Mock()

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        request,
    )

    with pytest.raises(ValueError):
        jc.get_deployment_build("not-a-number")

    request.assert_not_called()


# ------------------------------------------------------------
# Device deployment history safeguards
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "device",
    ["R5", "S5", ""],
)
def test_recent_device_builds_rejects_unmanaged_device(
    monkeypatch,
    device,
):
    """Deployment history is restricted to managed devices."""

    request = Mock()

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        request,
    )

    with pytest.raises(
        RuntimeError,
        match="Device is not allowed for deployment",
    ):
        jc.get_recent_device_builds(device)

    request.assert_not_called()


@pytest.mark.parametrize(
    "limit",
    [0, -1],
)
def test_recent_device_builds_rejects_invalid_limit(
    monkeypatch,
    limit,
):
    request = Mock()

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        request,
    )

    with pytest.raises(
        RuntimeError,
        match="Deployment history limit must be at least 1",
    ):
        jc.get_recent_device_builds(
            "R1",
            limit=limit,
        )

    request.assert_not_called()


# ------------------------------------------------------------
# Filter, sort, and limit deployment history
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "limit, expected_numbers",
    [
        (1, [15]),
        (3, [15, 10, 4]),
        (6, [15, 10, 4, None]),
    ],
)
def test_recent_device_builds_filters_sorts_and_limits(
    monkeypatch,
    fake_jenkins_config,
    limit,
    expected_numbers,
):
    """Only matching device builds should be returned, newest first."""

    build_data = {
        "builds": [
            {
                "number": 4,
                "actions": [
                    {
                        "parameters": [
                            {"name": "DEVICE", "value": "R1"},
                            {"name": "ACTION", "value": "dry-run"},
                            {"value": "ignored"},
                        ]
                    }
                ],
            },
            {
                "number": 30,
                "actions": [
                    {
                        "parameters": [
                            {"name": "DEVICE", "value": "R2"},
                        ]
                    }
                ],
            },
            {
                "number": 15,
                "actions": [
                    {
                        "parameters": [
                            {"name": "DEVICE", "value": "R1"},
                            {"name": "ACTION", "value": "preview"},
                        ]
                    }
                ],
            },
            {
                "number": 8,
            },
            {
                "number": 10,
                "actions": [
                    {},
                    {
                        "parameters": [
                            {"name": "DEVICE", "value": "R1"},
                            {"name": "ACTION", "value": "apply"},
                        ]
                    },
                ],
            },
            {
                "actions": [
                    {
                        "parameters": [
                            {"name": "DEVICE", "value": "R1"},
                        ]
                    }
                ],
            },
        ]
    }

    response = Mock()
    response.json.return_value = build_data

    request = Mock(return_value=response)

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        request,
    )

    results = jc.get_recent_device_builds(
        "R1",
        limit=limit,
    )

    assert [
        build.get("number")
        for build in results
    ] == expected_numbers

    assert all(
        build["parameters"]["DEVICE"] == "R1"
        for build in results
    )

    request.assert_called_once_with(
        "GET",
        "/job/deploy%20job/api/json"
        "?tree=builds["
        "number,url,building,result,timestamp,duration,"
        "actions[parameters[name,value]]"
        "]",
    )


# ------------------------------------------------------------
# Empty deployment history
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "build_data",
    [
        {},
        {"builds": []},
    ],
)
def test_recent_device_builds_empty_history(
    monkeypatch,
    fake_jenkins_config,
    build_data,
):
    response = Mock()
    response.json.return_value = build_data

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        Mock(return_value=response),
    )

    assert jc.get_recent_device_builds("R1") == []


# ------------------------------------------------------------
# Invalid build-history JSON
# ------------------------------------------------------------

def test_recent_device_builds_invalid_json(
    monkeypatch,
    fake_jenkins_config,
):
    original_error = ValueError(
        "Simulated invalid build-list JSON"
    )

    response = Mock()
    response.json.side_effect = original_error

    monkeypatch.setattr(
        jc,
        "jenkins_request",
        Mock(return_value=response),
    )

    with pytest.raises(
        RuntimeError,
        match="Jenkins returned invalid build-list JSON",
    ) as exc:
        jc.get_recent_device_builds("R1")

    assert exc.value.__cause__ is original_error


# ------------------------------------------------------------
# Latest device build
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "builds, expected",
    [
        (
            [
                {
                    "number": 25,
                    "result": "SUCCESS",
                }
            ],
            {
                "number": 25,
                "result": "SUCCESS",
            },
        ),
        (
            [],
            None,
        ),
    ],
)
def test_get_latest_device_build(
    monkeypatch,
    builds,
    expected,
):
    """Return the newest build, or None when history is empty."""

    history = Mock(return_value=builds)

    monkeypatch.setattr(
        jc,
        "get_recent_device_builds",
        history,
    )

    assert jc.get_latest_device_build("R1") == expected

    history.assert_called_once_with(
        "R1",
        limit=1,
    )
