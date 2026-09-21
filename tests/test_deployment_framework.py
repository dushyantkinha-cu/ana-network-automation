from copy import deepcopy
from pathlib import Path

import pytest

import automation.deploy_config as deploy_cli
from automation.deployment.common import (
    DeploymentSafetyError,
    find_deployment_target,
    validate_device_for_deployment,
)


BASE_DEVICE = {
    "hostname": "R3",
    "device_id": 7,
    "status": "active",
    "manufacturer": "Cisco",
    "platform": "Cisco IOS-XE",
    "role": "Router",
    "management_ip": "172.20.20.9/24",
    "config_profile": "edge",
    "automation_managed": True,
}


@pytest.mark.parametrize(
    (
        "hostname",
        "platform",
        "profile",
        "adapter",
    ),
    [
        (
            "R1",
            "Arista EOS",
            "distribution",
            "arista",
        ),
        (
            "R3",
            "Cisco IOS-XE",
            "edge",
            "cisco",
        ),
        (
            "S4",
            "Nokia SR Linux",
            "core",
            "nokia",
        ),
    ],
)
def test_supported_platform_adapter_mapping(
    hostname,
    platform,
    profile,
    adapter,
):
    device = deepcopy(BASE_DEVICE)

    device["hostname"] = hostname
    device["platform"] = platform
    device["config_profile"] = profile

    target = validate_device_for_deployment(
        device
    )

    assert target.hostname == hostname
    assert target.adapter == adapter
    assert target.platform == platform
    assert target.config_profile == profile


def test_r5_is_explicitly_denied():
    with pytest.raises(
        DeploymentSafetyError,
        match="explicitly denied",
    ):
        find_deployment_target(
            {"devices": []},
            "R5",
        )


def test_staged_device_is_denied():
    device = deepcopy(BASE_DEVICE)
    device["status"] = "staged"

    with pytest.raises(
        DeploymentSafetyError,
        match="not deployable",
    ):
        validate_device_for_deployment(device)


def test_unmanaged_device_is_denied():
    device = deepcopy(BASE_DEVICE)
    device["automation_managed"] = False

    with pytest.raises(
        DeploymentSafetyError,
        match="automation_managed",
    ):
        validate_device_for_deployment(device)


def test_missing_management_ip_is_denied():
    device = deepcopy(BASE_DEVICE)
    device["management_ip"] = None

    with pytest.raises(
        DeploymentSafetyError,
        match="management IP is missing",
    ):
        validate_device_for_deployment(device)


def test_unsupported_platform_is_denied():
    device = deepcopy(BASE_DEVICE)
    device["platform"] = "Unknown NOS"

    with pytest.raises(
        DeploymentSafetyError,
        match="unsupported platform",
    ):
        validate_device_for_deployment(device)


def test_unsupported_profile_is_denied():
    device = deepcopy(BASE_DEVICE)
    device["config_profile"] = "invalid-profile"

    with pytest.raises(
        DeploymentSafetyError,
        match="no supported template",
    ):
        validate_device_for_deployment(device)


def test_unknown_device_is_denied():
    with pytest.raises(
        DeploymentSafetyError,
        match="not found in managed NetBox inventory",
    ):
        find_deployment_target(
            {"devices": []},
            "DOES-NOT-EXIST",
        )


def test_cli_requires_dry_run_before_inventory(
    monkeypatch,
):
    inventory_called = False

    def forbidden_inventory():
        nonlocal inventory_called
        inventory_called = True
        raise AssertionError(
            "Inventory must not load without --dry-run."
        )

    monkeypatch.setattr(
        deploy_cli,
        "load_inventory",
        forbidden_inventory,
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "deploy_config.py",
            "--device",
            "R3",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        deploy_cli.main()

    assert exc.value.code == 2
    assert inventory_called is False


def test_r5_cli_never_reaches_renderer(
    monkeypatch,
):
    renderer_called = False

    def fake_inventory():
        return {
            "devices": [],
            "managed_device_count": 0,
        }

    def forbidden_renderer(hostname):
        nonlocal renderer_called
        renderer_called = True
        raise AssertionError(
            "Renderer must never run for R5."
        )

    monkeypatch.setattr(
        deploy_cli,
        "load_inventory",
        fake_inventory,
    )

    monkeypatch.setattr(
        deploy_cli,
        "render_device",
        forbidden_renderer,
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "deploy_config.py",
            "--device",
            "R5",
            "--dry-run",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        deploy_cli.main()

    assert exc.value.code == 1
    assert renderer_called is False


def test_dry_run_plan_is_non_destructive(
    monkeypatch,
    tmp_path,
    capsys,
):
    device = deepcopy(BASE_DEVICE)

    rendered = (
        tmp_path
        / "R3.cfg"
    )

    rendered.write_text(
        "hostname R3\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        deploy_cli,
        "load_inventory",
        lambda: {
            "devices": [device],
            "managed_device_count": 1,
        },
    )

    monkeypatch.setattr(
        deploy_cli,
        "render_device",
        lambda hostname: rendered,
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "deploy_config.py",
            "--device",
            "R3",
            "--dry-run",
        ],
    )

    deploy_cli.main()

    output = capsys.readouterr().out

    assert "Safety gates:        PASS" in output
    assert "Mode:                DRY RUN" in output
    assert (
        "No device connection was opened."
        in output
    )
    assert (
        "No device configuration was attempted."
        in output
    )
