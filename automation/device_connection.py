#!/usr/bin/env python3

import ipaddress
import os

from netmiko import ConnectHandler


PLATFORM_SETTINGS = {
    "Arista EOS": {
        "device_type": "arista_eos",
        "username_env": "ARISTA_USERNAME",
        "password_env": "ARISTA_PASSWORD",
        "enable": True,
    },
    "Cisco IOS-XE": {
        "device_type": "cisco_ios",
        "username_env": "CISCO_USERNAME",
        "password_env": "CISCO_PASSWORD",
        "enable": False,
    },
    "Nokia SR Linux": {
        "device_type": "nokia_srl",
        "username_env": "NOKIA_USERNAME",
        "password_env": "NOKIA_PASSWORD",
        "enable": False,
    },
}


class DeviceConnectionError(RuntimeError):
    """Raised when device connection parameters are invalid."""


def get_platform_settings(platform):
    settings = PLATFORM_SETTINGS.get(platform)

    if not settings:
        raise DeviceConnectionError(
            f"Unsupported platform: {platform!r}"
        )

    return settings


def get_credentials(settings):
    username_env = settings["username_env"]
    password_env = settings["password_env"]

    username = os.environ.get(username_env)
    password = os.environ.get(password_env)

    if not username:
        raise DeviceConnectionError(
            f"Required environment variable "
            f"{username_env} is not set."
        )

    if not password:
        raise DeviceConnectionError(
            f"Required environment variable "
            f"{password_env} is not set."
        )

    return username, password


def management_host(device):
    management_ip = device.get("management_ip")

    if not management_ip:
        hostname = device.get(
            "hostname",
            "<unknown>",
        )

        raise DeviceConnectionError(
            f"{hostname}: management IP is missing."
        )

    try:
        return str(
            ipaddress.ip_interface(
                management_ip
            ).ip
        )
    except ValueError as exc:
        raise DeviceConnectionError(
            f"Invalid management IP "
            f"{management_ip!r}."
        ) from exc


def build_connection_args(device):
    platform = device.get("platform")

    settings = get_platform_settings(
        platform
    )

    username, password = get_credentials(
        settings
    )

    connection_args = {
        "device_type": settings["device_type"],
        "host": management_host(device),
        "username": username,
        "password": password,
    }

    if settings["enable"]:
        connection_args["secret"] = password

    return connection_args


def open_connection(device):
    settings = get_platform_settings(
        device.get("platform")
    )

    connection = ConnectHandler(
        **build_connection_args(device)
    )

    try:
        if (
            settings["enable"]
            and not connection.check_enable_mode()
        ):
            connection.enable()

        return connection

    except Exception:
        connection.disconnect()
        raise
