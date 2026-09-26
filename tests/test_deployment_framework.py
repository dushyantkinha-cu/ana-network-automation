import json
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


def test_cli_apply_success_executes_transaction(
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

    def fake_execute(
        target,
        apply_device,
        rendered_path,
        rendered_config,
    ):
        observed["target"] = target
        observed["device"] = apply_device
        observed["rendered_path"] = (
            rendered_path
        )
        observed["rendered_config"] = (
            rendered_config
        )

        return (
            {
                "transaction_name": None,
                "command_count": 1,
                "sanitized_diff": None,
                "validation_passed": True,
                "committed": True,
                "persisted": True,
                "rolled_back": False,
                "status": "success",
                "error": None,
            },
            tmp_path / "deployment.json",
        )

    monkeypatch.setattr(
        deploy_cli,
        "execute_apply",
        fake_execute,
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

    deploy_cli.main()

    assert observed["target"].hostname == "R3"
    assert observed["device"] is device
    assert observed["rendered_path"] == rendered

    assert (
        observed["rendered_config"]
        == "hostname R3\n"
    )

    output = capsys.readouterr().out

    assert "Mode:                APPLY" in output
    assert "Status:              SUCCESS" in output
    assert "Committed:           True" in output
    assert "Persisted:           True" in output


def test_cli_apply_rollback_exits_failure(
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
        "execute_apply",
        lambda *args, **kwargs: (
            {
                "transaction_name": None,
                "command_count": 1,
                "sanitized_diff": None,
                "validation_passed": False,
                "committed": False,
                "persisted": False,
                "rolled_back": True,
                "status": "rolled_back",
                "error": None,
            },
            tmp_path / "deployment.json",
        ),
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

    output = capsys.readouterr().out

    assert "Status:              ROLLED_BACK" in output
    assert "Rolled back:         True" in output


def test_cli_apply_adapter_failure_exits_failure(
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
        "execute_apply",
        lambda *args, **kwargs: (
            {
                "transaction_name": None,
                "command_count": None,
                "sanitized_diff": None,
                "validation_passed": None,
                "committed": None,
                "persisted": None,
                "rolled_back": None,
                "status": "failed",
                "error": "forced failure",
            },
            tmp_path / "deployment.json",
        ),
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

    output = capsys.readouterr().out

    assert "Status:              FAILED" in output
    assert "Error:               forced failure" in output

@pytest.mark.parametrize(
    (
        "platform",
        "profile",
        "adapter",
        "apply_attribute",
    ),
    [
        (
            "Arista EOS",
            "distribution",
            "arista",
            "apply_arista",
        ),
        (
            "Cisco IOS-XE",
            "edge",
            "cisco",
            "apply_cisco",
        ),
        (
            "Nokia SR Linux",
            "core",
            "nokia",
            "apply_nokia",
        ),
    ],
)
def test_apply_dispatch_selects_vendor_adapter(
    monkeypatch,
    platform,
    profile,
    adapter,
    apply_attribute,
):
    device = deepcopy(BASE_DEVICE)

    device["platform"] = platform
    device["config_profile"] = profile

    target = validate_device_for_deployment(
        device
    )

    observed = {}

    def fake_apply(
        apply_device,
        rendered_config,
        post_validate,
    ):
        observed["device"] = apply_device
        observed["rendered_config"] = (
            rendered_config
        )
        observed["post_validate"] = (
            post_validate
        )

        return SimpleNamespace(
            adapter=adapter
        )

    def forbidden_apply(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Wrong vendor apply adapter called."
        )

    for attribute in (
        "apply_arista",
        "apply_cisco",
        "apply_nokia",
    ):
        monkeypatch.setattr(
            deploy_cli,
            attribute,
            forbidden_apply,
        )

    monkeypatch.setattr(
        deploy_cli,
        apply_attribute,
        fake_apply,
    )

    post_validate = lambda: True

    result = deploy_cli.dispatch_apply(
        target,
        device,
        "hostname TEST\n",
        post_validate,
    )

    assert result.adapter == adapter
    assert observed["device"] is device

    assert (
        observed["rendered_config"]
        == "hostname TEST\n"
    )

    assert (
        observed["post_validate"]
        is post_validate
    )


def test_post_validation_uses_existing_workflow(
    monkeypatch,
    capsys,
):
    observed = {}

    def fake_run(
        command,
        **kwargs,
    ):
        observed["command"] = command
        observed["kwargs"] = kwargs

        return SimpleNamespace(
            returncode=0,
            stdout="Overall status: PASS\n",
            stderr="",
        )

    monkeypatch.setattr(
        deploy_cli.subprocess,
        "run",
        fake_run,
    )

    result = deploy_cli.run_post_validation(
        "R3"
    )

    assert result is True

    assert observed["command"] == [
        deploy_cli.sys.executable,
        str(deploy_cli.VALIDATION_SCRIPT),
        "--device",
        "R3",
        "--details",
    ]

    assert (
        observed["kwargs"]["cwd"]
        == deploy_cli.REPO_ROOT
    )

    assert (
        observed["kwargs"]["capture_output"]
        is True
    )

    assert (
        observed["kwargs"]["text"]
        is True
    )

    output = capsys.readouterr().out

    assert "Overall status: PASS" in output

@pytest.mark.parametrize(
    (
        "adapter",
        "result",
        "expected_transaction",
        "expected_committed",
        "expected_rolled_back",
        "expected_status",
    ),
    [
        (
            "arista",
            SimpleNamespace(
                session_name="ANA-R1-TEST",
                command_count=10,
                diff="sanitized arista diff",
                validation_passed=True,
                confirmed=True,
                persisted=True,
                rolled_back=False,
            ),
            "ANA-R1-TEST",
            True,
            False,
            "success",
        ),
        (
            "cisco",
            SimpleNamespace(
                command_count=20,
                validation_passed=True,
                confirmed=True,
                persisted=True,
                rolled_back=False,
            ),
            None,
            True,
            False,
            "success",
        ),
        (
            "nokia",
            SimpleNamespace(
                candidate_name="ANA-S4-TEST",
                command_count=30,
                diff="sanitized nokia diff",
                validation_passed=True,
                accepted=True,
                persisted=True,
                rejected=False,
            ),
            "ANA-S4-TEST",
            True,
            False,
            "success",
        ),
    ],
)
def test_normalize_successful_apply_result(
    adapter,
    result,
    expected_transaction,
    expected_committed,
    expected_rolled_back,
    expected_status,
):
    target = SimpleNamespace(
        hostname="TEST",
        adapter=adapter,
    )

    normalized = (
        deploy_cli.normalize_apply_result(
            target,
            result,
        )
    )

    assert (
        normalized["transaction_name"]
        == expected_transaction
    )
    assert (
        normalized["committed"]
        is expected_committed
    )
    assert (
        normalized["persisted"]
        is True
    )
    assert (
        normalized["rolled_back"]
        is expected_rolled_back
    )
    assert (
        normalized["status"]
        == expected_status
    )


@pytest.mark.parametrize(
    (
        "adapter",
        "result",
    ),
    [
        (
            "arista",
            SimpleNamespace(
                session_name="ANA-R1-TEST",
                command_count=10,
                diff="",
                validation_passed=False,
                confirmed=False,
                persisted=False,
                rolled_back=True,
            ),
        ),
        (
            "cisco",
            SimpleNamespace(
                command_count=20,
                validation_passed=False,
                confirmed=False,
                persisted=False,
                rolled_back=True,
            ),
        ),
        (
            "nokia",
            SimpleNamespace(
                candidate_name="ANA-S4-TEST",
                command_count=30,
                diff="",
                validation_passed=False,
                accepted=False,
                persisted=False,
                rejected=True,
            ),
        ),
    ],
)
def test_normalize_rollback_result(
    adapter,
    result,
):
    target = SimpleNamespace(
        hostname="TEST",
        adapter=adapter,
    )

    normalized = (
        deploy_cli.normalize_apply_result(
            target,
            result,
        )
    )

    assert normalized["status"] == "rolled_back"
    assert normalized["validation_passed"] is False
    assert normalized["committed"] is False
    assert normalized["persisted"] is False
    assert normalized["rolled_back"] is True


def test_write_deployment_report(
    monkeypatch,
    tmp_path,
):
    report_dir = (
        tmp_path
        / "deployment-reports"
    )

    rendered = tmp_path / "R3.cfg"
    rendered.write_text(
        "hostname R3\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        deploy_cli,
        "DEPLOYMENT_REPORT_DIR",
        report_dir,
    )

    target = SimpleNamespace(
        hostname="R3",
        device_id=7,
        status="active",
        platform="Cisco IOS-XE",
        config_profile="edge",
        adapter="cisco",
        management_ip="172.20.20.9/24",
    )

    normalized = {
        "transaction_name": None,
        "command_count": 1,
        "sanitized_diff": None,
        "validation_passed": True,
        "committed": True,
        "persisted": True,
        "rolled_back": False,
        "status": "success",
    }

    report_path = (
        deploy_cli.write_deployment_report(
            target,
            rendered,
            normalized,
        )
    )

    assert report_path.is_file()

    payload = json.loads(
        report_path.read_text(
            encoding="utf-8"
        )
    )

    assert payload["mode"] == "apply"
    assert payload["safety_gates"] == "pass"

    assert (
        payload["device"]["hostname"]
        == "R3"
    )

    assert (
        payload["result"]["status"]
        == "success"
    )

    assert (
        payload["overall_status"]
        == "success"
    )

    assert (
        payload["rendered_config"]["sha256"]
        == deploy_cli.sha256_file(rendered)
    )

    assert (
        report_dir.stat().st_mode & 0o777
        == 0o700
    )

    assert (
        report_path.stat().st_mode & 0o777
        == 0o600
    )


def test_execute_apply_success_writes_report(
    monkeypatch,
    tmp_path,
):
    device = deepcopy(BASE_DEVICE)

    target = validate_device_for_deployment(
        device
    )

    rendered = tmp_path / "R3.cfg"
    rendered.write_text(
        "hostname R3\n",
        encoding="utf-8",
    )

    report_dir = (
        tmp_path / "deployment-reports"
    )

    monkeypatch.setattr(
        deploy_cli,
        "DEPLOYMENT_REPORT_DIR",
        report_dir,
    )

    validation_calls = []

    monkeypatch.setattr(
        deploy_cli,
        "run_post_validation",
        lambda hostname: (
            validation_calls.append(hostname)
            or True
        ),
    )

    def fake_dispatch(
        apply_target,
        apply_device,
        rendered_config,
        post_validate,
    ):
        assert apply_target is target
        assert apply_device is device
        assert rendered_config == "hostname R3\n"
        assert post_validate() is True

        return SimpleNamespace(
            command_count=1,
            validation_passed=True,
            confirmed=True,
            persisted=True,
            rolled_back=False,
        )

    monkeypatch.setattr(
        deploy_cli,
        "dispatch_apply",
        fake_dispatch,
    )

    normalized, report_path = (
        deploy_cli.execute_apply(
            target,
            device,
            rendered,
            "hostname R3\n",
        )
    )

    assert validation_calls == ["R3"]
    assert normalized["status"] == "success"
    assert normalized["error"] is None
    assert report_path.is_file()


def test_execute_apply_rollback_writes_report(
    monkeypatch,
    tmp_path,
):
    device = deepcopy(BASE_DEVICE)

    target = validate_device_for_deployment(
        device
    )

    rendered = tmp_path / "R3.cfg"
    rendered.write_text(
        "hostname R3\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        deploy_cli,
        "DEPLOYMENT_REPORT_DIR",
        tmp_path / "deployment-reports",
    )

    monkeypatch.setattr(
        deploy_cli,
        "dispatch_apply",
        lambda *args, **kwargs: SimpleNamespace(
            command_count=1,
            validation_passed=False,
            confirmed=False,
            persisted=False,
            rolled_back=True,
        ),
    )

    normalized, report_path = (
        deploy_cli.execute_apply(
            target,
            device,
            rendered,
            "hostname R3\n",
        )
    )

    assert normalized["status"] == "rolled_back"
    assert normalized["rolled_back"] is True
    assert report_path.is_file()

    payload = json.loads(
        report_path.read_text(
            encoding="utf-8"
        )
    )

    assert (
        payload["overall_status"]
        == "rolled_back"
    )


def test_execute_apply_error_writes_failed_report(
    monkeypatch,
    tmp_path,
):
    device = deepcopy(BASE_DEVICE)

    target = validate_device_for_deployment(
        device
    )

    rendered = tmp_path / "R3.cfg"
    rendered.write_text(
        "hostname R3\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        deploy_cli,
        "DEPLOYMENT_REPORT_DIR",
        tmp_path / "deployment-reports",
    )

    def failed_dispatch(
        *args,
        **kwargs,
    ):
        raise deploy_cli.CiscoApplyError(
            "forced adapter failure"
        )

    monkeypatch.setattr(
        deploy_cli,
        "dispatch_apply",
        failed_dispatch,
    )

    normalized, report_path = (
        deploy_cli.execute_apply(
            target,
            device,
            rendered,
            "hostname R3\n",
        )
    )

    assert normalized["status"] == "failed"

    assert (
        normalized["error"]
        == "forced adapter failure"
    )

    payload = json.loads(
        report_path.read_text(
            encoding="utf-8"
        )
    )

    assert payload["overall_status"] == "failed"

    assert (
        payload["result"]["error"]
        == "forced adapter failure"
    )
