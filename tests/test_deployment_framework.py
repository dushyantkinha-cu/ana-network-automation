from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

import automation.deploy_config as deploy_cli
from automation.deployment.common import (
    DeploymentSafetyError,
    find_deployment_target,
    validate_apply_confirmation,
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


def test_arista_preview_dispatches_safely(
    monkeypatch,
    tmp_path,
    capsys,
):
    device = deepcopy(BASE_DEVICE)

    device.update(
        {
            "hostname": "R1",
            "device_id": 1,
            "manufacturer": "Arista",
            "platform": "Arista EOS",
            "management_ip": "172.20.20.14/24",
            "config_profile": "distribution",
        }
    )

    rendered = tmp_path / "R1.cfg"

    rendered.write_text(
        "hostname R1\n",
        encoding="utf-8",
    )

    observed = {}

    def fake_preview(
        preview_device,
        rendered_config,
    ):
        observed["hostname"] = (
            preview_device["hostname"]
        )

        observed["rendered_config"] = (
            rendered_config
        )

        return SimpleNamespace(
            session_name="ANA-R1-TEST",
            command_count=1,
            diff="",
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
        deploy_cli,
        "preview_arista",
        fake_preview,
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "deploy_config.py",
            "--device",
            "R1",
            "--preview",
        ],
    )

    deploy_cli.main()

    output = capsys.readouterr().out

    assert observed["hostname"] == "R1"

    assert (
        observed["rendered_config"]
        == "hostname R1\n"
    )

    assert (
        "Safety gates:        PASS"
        in output
    )

    assert (
        "Mode:                PREVIEW"
        in output
    )

    assert (
        "Session:             ANA-R1-TEST"
        in output
    )

    assert "<no diff>" in output

    assert (
        "Preview session was aborted."
        in output
    )

    assert (
        "No configuration was committed."
        in output
    )




def test_dry_run_never_calls_preview(
    monkeypatch,
    tmp_path,
):
    device = deepcopy(BASE_DEVICE)

    rendered = tmp_path / "R3.cfg"

    rendered.write_text(
        "hostname R3\n",
        encoding="utf-8",
    )

    def forbidden_preview(
        device,
        rendered_config,
    ):
        raise AssertionError(
            "Dry-run must never call "
            "a deployment adapter."
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
        deploy_cli,
        "preview_arista",
        forbidden_preview,
    )
    monkeypatch.setattr(
        deploy_cli,
        "preview_cisco",
        forbidden_preview,
    )
    monkeypatch.setattr(
        deploy_cli,
        "preview_nokia",
        forbidden_preview,
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


def test_cisco_preview_dispatches_safely(
    monkeypatch,
    tmp_path,
    capsys,
):
    device = deepcopy(BASE_DEVICE)

    rendered = tmp_path / "R3.cfg"

    rendered.write_text(
        "hostname R3\n",
        encoding="utf-8",
    )

    observed = {}

    def fake_preview(
        preview_device,
        rendered_config,
    ):
        observed["hostname"] = (
            preview_device["hostname"]
        )

        observed["rendered_config"] = (
            rendered_config
        )

        return SimpleNamespace(
            command_count=1,
            rollback_started=True,
            rollback_completed=True,
            config_restored=True,
            before_hash="abc123",
            after_hash="abc123",
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
        deploy_cli,
        "preview_cisco",
        fake_preview,
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "deploy_config.py",
            "--device",
            "R3",
            "--preview",
        ],
    )

    deploy_cli.main()

    output = capsys.readouterr().out

    assert observed["hostname"] == "R3"

    assert (
        observed["rendered_config"]
        == "hostname R3\n"
    )

    assert (
        "Adapter:             cisco"
        in output
    )

    assert (
        "Mode:                PREVIEW"
        in output
    )

    assert (
        "Rollback started:    True"
        in output
    )

    assert (
        "Rollback completed:  True"
        in output
    )

    assert (
        "Config restored:     True"
        in output
    )

    assert (
        "Before hash:         abc123"
        in output
    )

    assert (
        "After hash:          abc123"
        in output
    )

    assert (
        "No configuration was confirmed or saved."
        in output
    )


def test_nokia_preview_dispatches_safely(
    monkeypatch,
    tmp_path,
    capsys,
):
    device = deepcopy(BASE_DEVICE)

    device.update(
        {
            "hostname": "S4",
            "device_id": 9,
            "manufacturer": "Nokia",
            "platform": "Nokia SR Linux",
            "management_ip": "172.20.20.4/24",
            "config_profile": "core",
        }
    )

    rendered = tmp_path / "S4.cfg"

    rendered.write_text(
        "interface lo0 {\n"
        "}\n",
        encoding="utf-8",
    )

    observed = {}

    def fake_preview(
        preview_device,
        rendered_config,
    ):
        observed["hostname"] = (
            preview_device["hostname"]
        )

        observed["rendered_config"] = (
            rendered_config
        )

        return SimpleNamespace(
            candidate_name="ANA-S4-TEST",
            command_count=2,
            validation_passed=True,
            diff="",
            discarded=True,
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
        deploy_cli,
        "preview_nokia",
        fake_preview,
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "deploy_config.py",
            "--device",
            "S4",
            "--preview",
        ],
    )

    deploy_cli.main()

    output = capsys.readouterr().out

    assert observed["hostname"] == "S4"

    assert (
        observed["rendered_config"]
        == "interface lo0 {\n}\n"
    )

    assert (
        "Adapter:             nokia"
        in output
    )

    assert (
        "Mode:                PREVIEW"
        in output
    )

    assert (
        "Candidate:           ANA-S4-TEST"
        in output
    )

    assert (
        "CLI commands staged: 2"
        in output
    )

    assert (
        "Validation passed:   True"
        in output
    )

    assert (
        "Discarded:           True"
        in output
    )

    assert (
        "=== SANITIZED CANDIDATE DIFF ==="
        in output
    )

    assert "<no diff>" in output

    assert (
        "No configuration was committed."
        in output
    )

def test_apply_confirmation_exact_match():
    assert (
        validate_apply_confirmation(
            "R3",
            "R3",
        )
        == "R3"
    )


def test_apply_confirmation_mismatch_is_denied():
    with pytest.raises(
        DeploymentSafetyError,
        match="does not exactly match",
    ):
        validate_apply_confirmation(
            "R3",
            "r3",
        )


def test_apply_requires_confirmation_before_inventory(
    monkeypatch,
):
    inventory_called = False

    def forbidden_inventory():
        nonlocal inventory_called
        inventory_called = True

        raise AssertionError(
            "Inventory must not load when "
            "apply confirmation is missing."
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
            "--apply",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        deploy_cli.main()

    assert exc.value.code == 1
    assert inventory_called is False


def test_apply_confirmation_mismatch_before_inventory(
    monkeypatch,
):
    inventory_called = False

    def forbidden_inventory():
        nonlocal inventory_called
        inventory_called = True

        raise AssertionError(
            "Inventory must not load when "
            "apply confirmation mismatches."
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
            "--apply",
            "--confirm-device",
            "r3",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        deploy_cli.main()

    assert exc.value.code == 1
    assert inventory_called is False


def test_confirm_device_rejected_outside_apply(
    monkeypatch,
):
    inventory_called = False

    def forbidden_inventory():
        nonlocal inventory_called
        inventory_called = True

        raise AssertionError(
            "Inventory must not load for an "
            "invalid confirmation argument."
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
            "--dry-run",
            "--confirm-device",
            "R3",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        deploy_cli.main()

    assert exc.value.code == 1
    assert inventory_called is False


def test_apply_cannot_reach_live_adapter(
    monkeypatch,
    tmp_path,
):
    device = deepcopy(BASE_DEVICE)

    rendered = tmp_path / "R3.cfg"

    rendered.write_text(
        "hostname R3\n",
        encoding="utf-8",
    )

    adapter_called = False

    def forbidden_adapter(
        *args,
        **kwargs,
    ):
        nonlocal adapter_called
        adapter_called = True

        raise AssertionError(
            "Stage 7F.1 apply mode must never "
            "call a live adapter."
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
        deploy_cli,
        "preview_arista",
        forbidden_adapter,
    )

    monkeypatch.setattr(
        deploy_cli,
        "preview_cisco",
        forbidden_adapter,
    )

    monkeypatch.setattr(
        deploy_cli,
        "preview_nokia",
        forbidden_adapter,
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "deploy_config.py",
            "--device",
            "R3",
            "--apply",
            "--confirm-device",
            "R3",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        deploy_cli.main()

    assert exc.value.code == 1
    assert adapter_called is False
