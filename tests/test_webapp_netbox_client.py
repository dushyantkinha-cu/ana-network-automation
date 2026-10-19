"""Unit tests for webapp.clients.netbox."""

from unittest.mock import Mock

import pytest

from webapp.clients import netbox as nc


# ------------------------------------------------------------
# Test environment
# ------------------------------------------------------------

@pytest.fixture
def mock_netbox_environment(monkeypatch):
    """Use dummy credentials and a simulated NetBox URL."""

    monkeypatch.setenv("NETBOX_TOKEN", "unit-test-token")
    monkeypatch.setattr(
        nc,
        "NETBOX_URL",
        "http://netbox.example",
    )

    return {
        "Authorization": "Token unit-test-token",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# ------------------------------------------------------------
# Authentication headers
# ------------------------------------------------------------

def test_netbox_headers_success(mock_netbox_environment):
    """NetBox API headers must contain the configured token."""

    assert nc.netbox_headers() == mock_netbox_environment


@pytest.mark.parametrize(
    "token",
    [None, ""],
)
def test_netbox_headers_missing_token(monkeypatch, token):
    """Requests must be rejected when authentication is unavailable."""

    if token is None:
        monkeypatch.delenv("NETBOX_TOKEN", raising=False)
    else:
        monkeypatch.setenv("NETBOX_TOKEN", token)

    with pytest.raises(
        RuntimeError,
        match="NETBOX_TOKEN is not available",
    ):
        nc.netbox_headers()


# ------------------------------------------------------------
# Successful GET requests
# ------------------------------------------------------------

def test_netbox_get_success(
    monkeypatch, mock_netbox_environment
):
    """GET must return parsed JSON from NetBox."""

    expected = {
        "count": 1,
        "results": [
            {"id": 1, "name": "R1"},
        ],
    }

    response = Mock()
    response.json.return_value = expected

    request = Mock(return_value=response)

    monkeypatch.setattr(nc.requests, "get", request)

    result = nc.netbox_get("/api/dcim/devices/")

    assert result == expected

    request.assert_called_once_with(
        "http://netbox.example/api/dcim/devices/",
        headers=mock_netbox_environment,
        timeout=10,
    )

    response.raise_for_status.assert_called_once_with()
    response.json.assert_called_once_with()


# ------------------------------------------------------------
# Successful PATCH requests
# ------------------------------------------------------------

def test_netbox_patch_success(
    monkeypatch, mock_netbox_environment
):
    """PATCH must send the payload and return the updated object."""

    payload = {
        "description": "Updated by unit test",
    }

    expected = {
        "id": 10,
        "description": "Updated by unit test",
    }

    response = Mock()
    response.json.return_value = expected

    request = Mock(return_value=response)

    monkeypatch.setattr(nc.requests, "patch", request)

    result = nc.netbox_patch(
        "/api/dcim/interfaces/10/",
        payload,
    )

    assert result == expected

    request.assert_called_once_with(
        "http://netbox.example/api/dcim/interfaces/10/",
        headers=mock_netbox_environment,
        json=payload,
        timeout=10,
    )

    response.raise_for_status.assert_called_once_with()
    response.json.assert_called_once_with()


# ------------------------------------------------------------
# Successful POST requests
# ------------------------------------------------------------

def test_netbox_post_success(
    monkeypatch, mock_netbox_environment
):
    """POST must send JSON and return the created object."""

    payload = {
        "name": "TEST-SITE",
        "slug": "test-site",
    }

    expected = {
        "id": 100,
        **payload,
    }

    response = Mock()
    response.json.return_value = expected

    request = Mock(return_value=response)

    monkeypatch.setattr(nc.requests, "post", request)

    result = nc.netbox_post(
        "/api/dcim/sites/",
        payload,
    )

    assert result == expected

    request.assert_called_once_with(
        "http://netbox.example/api/dcim/sites/",
        headers=mock_netbox_environment,
        json=payload,
        timeout=10,
    )

    response.raise_for_status.assert_called_once_with()
    response.json.assert_called_once_with()


# ------------------------------------------------------------
# Successful DELETE requests
# ------------------------------------------------------------

def test_netbox_delete_204(
    monkeypatch, mock_netbox_environment
):
    """HTTP 204 means deletion succeeded without response content."""

    response = Mock()
    response.status_code = 204

    request = Mock(return_value=response)

    monkeypatch.setattr(nc.requests, "delete", request)

    result = nc.netbox_delete("/api/dcim/sites/100/")

    assert result is None

    request.assert_called_once_with(
        "http://netbox.example/api/dcim/sites/100/",
        headers=mock_netbox_environment,
        timeout=10,
    )

    response.raise_for_status.assert_not_called()
    response.json.assert_not_called()


def test_netbox_delete_other_success_status(
    monkeypatch, mock_netbox_environment
):
    """Non-204 responses must still be checked for HTTP errors."""

    response = Mock()
    response.status_code = 200

    request = Mock(return_value=response)

    monkeypatch.setattr(nc.requests, "delete", request)

    result = nc.netbox_delete("/api/dcim/sites/100/")

    assert result is None

    request.assert_called_once_with(
        "http://netbox.example/api/dcim/sites/100/",
        headers=mock_netbox_environment,
        timeout=10,
    )

    response.raise_for_status.assert_called_once_with()
    response.json.assert_not_called()


# ------------------------------------------------------------
# Simulated network failures for all HTTP methods
# ------------------------------------------------------------

import requests


@pytest.mark.parametrize(
    "method",
    ["get", "patch", "post", "delete"],
)
@pytest.mark.parametrize(
    "error_type",
    [
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
    ],
)
def test_netbox_network_failure(
    monkeypatch,
    mock_netbox_environment,
    method,
    error_type,
):
    """All HTTP methods must convert network failures to clear errors."""

    original_error = error_type(
        "Simulated NetBox network failure"
    )

    request = Mock(side_effect=original_error)

    monkeypatch.setattr(
        nc.requests,
        method,
        request,
    )

    client_function = getattr(
        nc,
        f"netbox_{method}",
    )

    with pytest.raises(
        RuntimeError,
        match=f"NetBox {method.upper()} failed",
    ) as exc:
        if method in ("post", "patch"):
            client_function(
                "/api/dcim/devices/",
                {"description": "Unit test"},
            )
        else:
            client_function(
                "/api/dcim/devices/",
            )

    # Verify that the original Requests exception is preserved.
    assert exc.value.__cause__ is original_error

    request.assert_called_once()


# ------------------------------------------------------------
# POST and DELETE HTTP error-response handling
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "method",
    ["post", "delete"],
)
@pytest.mark.parametrize(
    "response_text, expected_detail",
    [
        (
            "  Device name already exists  ",
            "Device name already exists",
        ),
        (
            "  \n  ",
            "Simulated HTTP 400",
        ),
    ],
)
def test_netbox_write_http_error_details(
    monkeypatch,
    mock_netbox_environment,
    method,
    response_text,
    expected_detail,
):
    """POST and DELETE must report useful NetBox error details."""

    error_response = Mock()
    error_response.text = response_text

    original_error = requests.exceptions.HTTPError(
        "Simulated HTTP 400",
        response=error_response,
    )

    response = Mock()
    response.status_code = 400
    response.raise_for_status.side_effect = original_error

    request = Mock(return_value=response)

    monkeypatch.setattr(
        nc.requests,
        method,
        request,
    )

    with pytest.raises(RuntimeError) as exc:
        if method == "post":
            nc.netbox_post(
                "/api/dcim/sites/",
                {"name": "TEST-SITE"},
            )
        else:
            nc.netbox_delete(
                "/api/dcim/sites/100/",
            )

    assert str(exc.value) == (
        f"NetBox {method.upper()} failed: {expected_detail}"
    )

    assert exc.value.__cause__ is original_error

    request.assert_called_once()
    response.raise_for_status.assert_called_once_with()
