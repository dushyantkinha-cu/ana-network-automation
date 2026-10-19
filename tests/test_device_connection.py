"""Unit tests for automation.device_connection."""

import pytest

from automation import device_connection as dc


# ------------------------------------------------------------
# Platform settings
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "platform, expected_type, enable",
    [
        ("Arista EOS", "arista_eos", True),
        ("Cisco IOS-XE", "cisco_ios", False),
        ("Nokia SR Linux", "nokia_srl", False),
    ],
)
def test_get_platform_settings(platform, expected_type, enable):
    settings = dc.get_platform_settings(platform)

    assert settings["device_type"] == expected_type
    assert settings["enable"] is enable


def test_get_platform_settings_unsupported():
    with pytest.raises(
        dc.DeviceConnectionError,
        match="Unsupported platform",
    ):
        dc.get_platform_settings("Unsupported OS")


# ------------------------------------------------------------
# Credential handling
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "platform",
    ["Arista EOS", "Cisco IOS-XE", "Nokia SR Linux"],
)
def test_get_credentials_success(monkeypatch, platform):
    settings = dc.get_platform_settings(platform)

    monkeypatch.setenv(settings["username_env"], "unit-user")
    monkeypatch.setenv(settings["password_env"], "unit-password")

    username, password = dc.get_credentials(settings)

    assert username == "unit-user"
    assert password == "unit-password"


@pytest.mark.parametrize(
    "missing_key",
    ["username_env", "password_env"],
)
def test_get_credentials_missing(monkeypatch, missing_key):
    settings = dc.get_platform_settings("Arista EOS")

    monkeypatch.setenv("ARISTA_USERNAME", "unit-user")
    monkeypatch.setenv("ARISTA_PASSWORD", "unit-password")

    missing_variable = settings[missing_key]
    monkeypatch.delenv(missing_variable)

    with pytest.raises(
        dc.DeviceConnectionError,
        match=f"{missing_variable} is not set",
    ):
        dc.get_credentials(settings)


# ------------------------------------------------------------
# Management IP validation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "management_ip, expected",
    [
        ("172.20.20.14/24", "172.20.20.14"),
        ("2001:db8::14/64", "2001:db8::14"),
    ],
)
def test_management_host_valid(management_ip, expected):
    device = {
        "hostname": "R1",
        "management_ip": management_ip,
    }

    assert dc.management_host(device) == expected


def test_management_host_missing():
    device = {"hostname": "R1"}

    with pytest.raises(
        dc.DeviceConnectionError,
        match="R1: management IP is missing",
    ):
        dc.management_host(device)


@pytest.mark.parametrize(
    "invalid_ip",
    [
        "not-an-ip",
        "172.20.20.14/99",
    ],
)
def test_management_host_invalid(invalid_ip):
    device = {
        "hostname": "R1",
        "management_ip": invalid_ip,
    }

    with pytest.raises(
        dc.DeviceConnectionError,
        match="Invalid management IP",
    ):
        dc.management_host(device)


# ------------------------------------------------------------
# Netmiko connection argument construction
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "platform, expected_type, username_env, password_env, enable",
    [
        (
            "Arista EOS",
            "arista_eos",
            "ARISTA_USERNAME",
            "ARISTA_PASSWORD",
            True,
        ),
        (
            "Cisco IOS-XE",
            "cisco_ios",
            "CISCO_USERNAME",
            "CISCO_PASSWORD",
            False,
        ),
        (
            "Nokia SR Linux",
            "nokia_srl",
            "NOKIA_USERNAME",
            "NOKIA_PASSWORD",
            False,
        ),
    ],
)
def test_build_connection_args(
    monkeypatch,
    platform,
    expected_type,
    username_env,
    password_env,
    enable,
):
    monkeypatch.setenv(username_env, "unit-user")
    monkeypatch.setenv(password_env, "unit-password")

    device = {
        "hostname": "TEST1",
        "platform": platform,
        "management_ip": "172.20.20.100/24",
    }

    args = dc.build_connection_args(device)

    assert args["device_type"] == expected_type
    assert args["host"] == "172.20.20.100"
    assert args["username"] == "unit-user"
    assert args["password"] == "unit-password"

    if enable:
        assert args["secret"] == "unit-password"
    else:
        assert "secret" not in args


# ------------------------------------------------------------
# Mocked Netmiko connection handling
# ------------------------------------------------------------

@pytest.fixture
def mocked_handler(monkeypatch):
    """Replace ConnectHandler with an in-memory mock."""
    from unittest.mock import Mock

    for vendor in ("ARISTA", "CISCO", "NOKIA"):
        monkeypatch.setenv(
            f"{vendor}_USERNAME", "unit-user"
        )
        monkeypatch.setenv(
            f"{vendor}_PASSWORD", "unit-password"
        )

    connection = Mock()
    handler = Mock(return_value=connection)

    monkeypatch.setattr(dc, "ConnectHandler", handler)

    return connection, handler


@pytest.mark.parametrize(
    "already_enabled",
    [True, False],
)
def test_open_connection_arista(
    mocked_handler, already_enabled
):
    """Arista should enter enable mode only when necessary."""

    connection, handler = mocked_handler
    connection.check_enable_mode.return_value = already_enabled

    device = {
        "hostname": "R1",
        "platform": "Arista EOS",
        "management_ip": "172.20.20.14/24",
    }

    result = dc.open_connection(device)

    assert result is connection

    handler.assert_called_once_with(
        device_type="arista_eos",
        host="172.20.20.14",
        username="unit-user",
        password="unit-password",
        secret="unit-password",
    )

    connection.check_enable_mode.assert_called_once_with()

    if already_enabled:
        connection.enable.assert_not_called()
    else:
        connection.enable.assert_called_once_with()

    connection.disconnect.assert_not_called()


@pytest.mark.parametrize(
    "platform, device_type",
    [
        ("Cisco IOS-XE", "cisco_ios"),
        ("Nokia SR Linux", "nokia_srl"),
    ],
)
def test_open_connection_without_enable(
    mocked_handler, platform, device_type
):
    """Cisco and Nokia should not use Arista enable-mode logic."""

    connection, handler = mocked_handler

    device = {
        "hostname": "TEST1",
        "platform": platform,
        "management_ip": "172.20.20.100/24",
    }

    result = dc.open_connection(device)

    assert result is connection

    handler.assert_called_once_with(
        device_type=device_type,
        host="172.20.20.100",
        username="unit-user",
        password="unit-password",
    )

    connection.check_enable_mode.assert_not_called()
    connection.enable.assert_not_called()
    connection.disconnect.assert_not_called()


@pytest.mark.parametrize(
    "failed_method",
    ["check_enable_mode", "enable"],
)
def test_open_connection_disconnects_on_enable_failure(
    mocked_handler, failed_method
):
    """Failures after connecting must trigger disconnection."""

    connection, handler = mocked_handler

    device = {
        "hostname": "R1",
        "platform": "Arista EOS",
        "management_ip": "172.20.20.14/24",
    }

    if failed_method == "check_enable_mode":
        connection.check_enable_mode.side_effect = RuntimeError(
            "Simulated enable-mode check failure"
        )
    else:
        connection.check_enable_mode.return_value = False
        connection.enable.side_effect = RuntimeError(
            "Simulated enable failure"
        )

    with pytest.raises(RuntimeError, match="Simulated"):
        dc.open_connection(device)

    handler.assert_called_once()
    connection.disconnect.assert_called_once_with()


def test_open_connection_handler_failure(mocked_handler):
    """Connection establishment errors must propagate."""

    connection, handler = mocked_handler

    handler.side_effect = ConnectionError(
        "Simulated SSH connection failure"
    )

    device = {
        "hostname": "R1",
        "platform": "Arista EOS",
        "management_ip": "172.20.20.14/24",
    }

    with pytest.raises(
        ConnectionError,
        match="Simulated SSH connection failure",
    ):
        dc.open_connection(device)

    handler.assert_called_once()

    # No connection object was established, so there is
    # nothing for open_connection() to disconnect.
    connection.disconnect.assert_not_called()
