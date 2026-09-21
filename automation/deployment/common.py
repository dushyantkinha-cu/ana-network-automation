#!/usr/bin/env python3

from dataclasses import dataclass

from automation.render_config import TEMPLATE_MAP


DENIED_HOSTNAMES = {
    "R5",
}

DEPLOYABLE_STATUSES = {
    "active",
}

PLATFORM_ADAPTERS = {
    "Arista EOS": "arista",
    "Cisco IOS-XE": "cisco",
    "Nokia SR Linux": "nokia",
}


class DeploymentSafetyError(RuntimeError):
    """Raised when a deployment target fails a safety gate."""


@dataclass(frozen=True)
class DeploymentTarget:
    hostname: str
    device_id: int
    status: str
    manufacturer: str | None
    platform: str
    role: str | None
    management_ip: str
    config_profile: str
    adapter: str


def validate_device_for_deployment(
    device,
    requested_hostname=None,
):
    hostname = device.get("hostname")

    if requested_hostname in DENIED_HOSTNAMES:
        raise DeploymentSafetyError(
            f"Deployment to {requested_hostname!r} is explicitly denied."
        )

    if hostname in DENIED_HOSTNAMES:
        raise DeploymentSafetyError(
            f"Deployment to {hostname!r} is explicitly denied."
        )

    if not hostname:
        raise DeploymentSafetyError(
            "Device does not have a hostname."
        )

    if device.get("automation_managed") is not True:
        raise DeploymentSafetyError(
            f"{hostname}: automation_managed is not true."
        )

    status = device.get("status")

    if status not in DEPLOYABLE_STATUSES:
        raise DeploymentSafetyError(
            f"{hostname}: device status {status!r} "
            "is not deployable."
        )

    device_id = device.get("device_id")

    if device_id is None:
        raise DeploymentSafetyError(
            f"{hostname}: NetBox device_id is missing."
        )

    management_ip = device.get("management_ip")

    if not management_ip:
        raise DeploymentSafetyError(
            f"{hostname}: management IP is missing."
        )

    platform = device.get("platform")

    adapter = PLATFORM_ADAPTERS.get(platform)

    if not adapter:
        raise DeploymentSafetyError(
            f"{hostname}: unsupported platform {platform!r}."
        )

    config_profile = device.get("config_profile")

    template_key = (
        platform,
        config_profile,
    )

    if template_key not in TEMPLATE_MAP:
        raise DeploymentSafetyError(
            f"{hostname}: no supported template for "
            f"platform={platform!r}, "
            f"profile={config_profile!r}."
        )

    return DeploymentTarget(
        hostname=hostname,
        device_id=device_id,
        status=status,
        manufacturer=device.get("manufacturer"),
        platform=platform,
        role=device.get("role"),
        management_ip=management_ip,
        config_profile=config_profile,
        adapter=adapter,
    )


def validate_apply_confirmation(
    requested_hostname,
    confirmed_hostname,
):
    if requested_hostname in DENIED_HOSTNAMES:
        raise DeploymentSafetyError(
            f"Deployment to "
            f"{requested_hostname!r} "
            "is explicitly denied."
        )

    if confirmed_hostname is None:
        raise DeploymentSafetyError(
            f"{requested_hostname}: --apply "
            "requires --confirm-device "
            f"{requested_hostname}."
        )

    if confirmed_hostname != requested_hostname:
        raise DeploymentSafetyError(
            f"{requested_hostname}: confirmation "
            f"hostname {confirmed_hostname!r} "
            "does not exactly match the "
            "requested device."
        )

    return confirmed_hostname


def find_deployment_target(
    inventory,
    hostname,
):
    if hostname in DENIED_HOSTNAMES:
        raise DeploymentSafetyError(
            f"Deployment to {hostname!r} is explicitly denied."
        )

    devices = inventory.get("devices")

    if not isinstance(devices, list):
        raise DeploymentSafetyError(
            "Managed inventory does not contain a valid device list."
        )

    for device in devices:
        if device.get("hostname") == hostname:
            return validate_device_for_deployment(
                device,
                requested_hostname=hostname,
            )

    raise DeploymentSafetyError(
        f"{hostname!r} was not found in managed NetBox inventory."
    )
