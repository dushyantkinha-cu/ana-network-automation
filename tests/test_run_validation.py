"""Unit tests for automation.run_validation."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from automation import run_validation as rv


# ------------------------------------------------------------
# Subprocess command execution
# ------------------------------------------------------------

def test_run_command_success(monkeypatch):
    """Successful subprocess results must be normalized."""

    runner = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout="  Rendered R1 successfully\n",
            stderr="",
        )
    )

    monkeypatch.setattr(rv.subprocess, "run", runner)

    command = [
        rv.sys.executable,
        str(rv.RENDER_SCRIPT),
        "--device",
        "R1",
    ]

    result = rv.run_command(command)

    assert result == {
        "command": [
            rv.sys.executable,
            "render_config.py",
            "--device",
            "R1",
        ],
        "returncode": 0,
        "stdout": "Rendered R1 successfully",
        "stderr": "",
    }

    runner.assert_called_once_with(
        command,
        capture_output=True,
        text=True,
        env=rv.os.environ,
        cwd=rv.REPO_ROOT,
    )


def test_run_command_failure(monkeypatch):
    """Failed subprocesses must return results without raising."""

    runner = Mock(
        return_value=SimpleNamespace(
            returncode=2,
            stdout="  partial output\n",
            stderr="  Simulated command failure\n",
        )
    )

    monkeypatch.setattr(rv.subprocess, "run", runner)

    result = rv.run_command([
        rv.sys.executable,
        str(rv.DRIFT_SCRIPT),
    ])

    assert result["command"][1] == "report_drift.py"
    assert result["returncode"] == 2
    assert result["stdout"] == "partial output"
    assert result["stderr"] == "Simulated command failure"

    runner.assert_called_once()


# ------------------------------------------------------------
# NetBox inventory loading
# ------------------------------------------------------------

def test_load_inventory_success(monkeypatch):
    """Valid JSON from the inventory script must be parsed."""

    expected = {
        "devices": [
            {
                "hostname": "R1",
                "platform": "Arista EOS",
                "management_ip": "192.0.2.10/24",
            }
        ]
    }

    runner = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps(expected),
            stderr="",
        )
    )

    monkeypatch.setattr(rv.subprocess, "run", runner)

    assert rv.load_inventory() == expected

    runner.assert_called_once_with(
        [
            rv.sys.executable,
            str(rv.INVENTORY_SCRIPT),
        ],
        capture_output=True,
        text=True,
        env=rv.os.environ,
        cwd=rv.REPO_ROOT,
    )


@pytest.mark.parametrize(
    "stderr, expected_error",
    [
        (
            "Simulated NetBox connection failure",
            "Simulated NetBox connection failure",
        ),
        (
            "",
            "Unable to retrieve NetBox inventory.",
        ),
    ],
)
def test_load_inventory_subprocess_failure(
    monkeypatch, capsys, stderr, expected_error
):
    """Inventory subprocess failures must stop validation."""

    monkeypatch.setattr(
        rv.subprocess,
        "run",
        Mock(
            return_value=SimpleNamespace(
                returncode=1,
                stdout="",
                stderr=stderr,
            )
        ),
    )

    with pytest.raises(SystemExit) as exc:
        rv.load_inventory()

    assert exc.value.code == 1
    assert expected_error in capsys.readouterr().err


def test_load_inventory_invalid_json(monkeypatch, capsys):
    """Malformed inventory JSON must stop validation."""

    monkeypatch.setattr(
        rv.subprocess,
        "run",
        Mock(
            return_value=SimpleNamespace(
                returncode=0,
                stdout="{ invalid json",
                stderr="",
            )
        ),
    )

    with pytest.raises(SystemExit) as exc:
        rv.load_inventory()

    assert exc.value.code == 1
    assert "Invalid inventory JSON" in (
        capsys.readouterr().err
    )


# ------------------------------------------------------------
# Validation step console output
# ------------------------------------------------------------

def test_print_step_success(capsys):
    """Successful steps must display PASS and their output."""

    result = {
        "returncode": 0,
        "stdout": "First result\nSecond result",
        "stderr": "",
    }

    rv.print_step("render R1", result)

    output = capsys.readouterr().out

    assert "PASS  render R1" in output
    assert "      First result" in output
    assert "      Second result" in output


def test_print_step_failure(capsys):
    """Failed steps must display FAIL and error details."""

    result = {
        "returncode": 1,
        "stdout": "Partial output",
        "stderr": "Error one\nError two",
    }

    rv.print_step("drift validation", result)

    output = capsys.readouterr().out

    assert "FAIL  drift validation" in output
    assert "      Partial output" in output
    assert "      Error one" in output
    assert "      Error two" in output


def test_print_step_empty_output(capsys):
    """Steps without output must print only their status."""

    result = {
        "returncode": 0,
        "stdout": "",
        "stderr": "",
    }

    rv.print_step("intent validation", result)

    assert capsys.readouterr().out == (
        "PASS  intent validation\n"
    )


# ------------------------------------------------------------
# Successful validation orchestration
# ------------------------------------------------------------

import stat
from pathlib import Path
from unittest.mock import call


@pytest.fixture
def isolated_validation_workflow(tmp_path, monkeypatch):
    """Mock external commands and isolate validation reports."""

    devices = [
        {
            "hostname": "R1",
            "platform": "Arista EOS",
            "management_ip": "192.0.2.11/24",
        },
        {
            "hostname": "R2",
            "platform": "Cisco IOS-XE",
            "management_ip": "192.0.2.12/24",
        },
    ]

    report_dir = tmp_path / "validation-reports"

    monkeypatch.setattr(rv, "REPORT_DIR", report_dir)

    monkeypatch.setattr(
        rv,
        "load_inventory",
        Mock(return_value={"devices": devices}),
    )

    def fake_run_command(command):
        return {
            "command": [
                command[0],
                Path(command[1]).name,
                *command[2:],
            ],
            "returncode": 0,
            "stdout": "Simulated successful operation",
            "stderr": "",
        }

    runner = Mock(side_effect=fake_run_command)
    monkeypatch.setattr(rv, "run_command", runner)

    return devices, runner, report_dir


def read_single_report(report_dir):
    """Load the one report created by a test."""

    reports = list(report_dir.glob("validation-*.json"))

    assert len(reports) == 1

    return reports[0], json.loads(reports[0].read_text())


# ------------------------------------------------------------
# Full successful validation
# ------------------------------------------------------------

def test_main_full_success(
    monkeypatch, capsys, isolated_validation_workflow
):
    """Full validation must execute all steps and save a PASS report."""

    devices, runner, report_dir = isolated_validation_workflow

    monkeypatch.setattr(
        rv.sys,
        "argv",
        ["run_validation.py"],
    )

    rv.main()

    expected_commands = [
        [
            rv.sys.executable,
            str(rv.RENDER_SCRIPT),
            "--device",
            "R1",
        ],
        [
            rv.sys.executable,
            str(rv.RENDER_SCRIPT),
            "--device",
            "R2",
        ],
        [
            rv.sys.executable,
            str(rv.COLLECT_SCRIPT),
        ],
        [
            rv.sys.executable,
            str(rv.VALIDATE_SCRIPT),
        ],
        [
            rv.sys.executable,
            str(rv.DRIFT_SCRIPT),
        ],
    ]

    assert runner.call_args_list == [
        call(command) for command in expected_commands
    ]

    report_path, report = read_single_report(report_dir)

    assert report_path.name.startswith("validation-all-")
    assert report["overall_status"] == "pass"
    assert report["scope"] == "all-managed-devices"
    assert report["timestamp_utc"]

    assert report["devices"] == devices

    assert [
        result["device"]
        for result in report["steps"]["render"]
    ] == ["R1", "R2"]

    assert all(
        result["returncode"] == 0
        for result in report["steps"]["render"]
    )

    assert report["steps"]["collect"]["returncode"] == 0
    assert report["steps"]["intent_validation"]["returncode"] == 0
    assert report["steps"]["drift"]["returncode"] == 0

    # Validation reports must have restricted permissions.
    assert stat.S_IMODE(report_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600

    output = capsys.readouterr().out

    assert "=== RENDER INTENDED CONFIGS ===" in output
    assert "=== COLLECT LIVE CONFIGS ===" in output
    assert "=== VALIDATE INTENDED PATHS ===" in output
    assert "=== CHECK SCOPED DRIFT ===" in output
    assert "Overall status: PASS" in output


# ------------------------------------------------------------
# Single-device validation with collection skipped
# ------------------------------------------------------------

def test_main_single_device_skip_collect_details(
    monkeypatch, capsys, isolated_validation_workflow
):
    """Selected-device validation must support skip and detail flags."""

    devices, runner, report_dir = isolated_validation_workflow

    monkeypatch.setattr(
        rv.sys,
        "argv",
        [
            "run_validation.py",
            "--device", "R2",
            "--skip-collect",
            "--details",
        ],
    )

    rv.main()

    assert runner.call_args_list == [
        call([
            rv.sys.executable,
            str(rv.RENDER_SCRIPT),
            "--device", "R2",
        ]),
        call([
            rv.sys.executable,
            str(rv.VALIDATE_SCRIPT),
            "--device", "R2",
            "--show-missing",
        ]),
        call([
            rv.sys.executable,
            str(rv.DRIFT_SCRIPT),
            "--device", "R2",
            "--details",
        ]),
    ]

    report_path, report = read_single_report(report_dir)

    assert report_path.name.startswith("validation-R2-")
    assert report["overall_status"] == "pass"
    assert report["scope"] == "R2"
    assert report["devices"] == [devices[1]]

    assert report["steps"]["collect"] == {
        "skipped": True
    }

    assert len(report["steps"]["render"]) == 1
    assert report["steps"]["render"][0]["device"] == "R2"

    output = capsys.readouterr().out

    assert "SKIP  live collection" in output
    assert "Overall status: PASS" in output


# ------------------------------------------------------------
# Single-device validation with collection enabled
# ------------------------------------------------------------

def test_main_single_device_with_collection(
    monkeypatch, isolated_validation_workflow
):
    """The selected device must be forwarded to every relevant step."""

    devices, runner, report_dir = isolated_validation_workflow

    monkeypatch.setattr(
        rv.sys,
        "argv",
        [
            "run_validation.py",
            "--device", "R1",
        ],
    )

    rv.main()

    assert runner.call_args_list == [
        call([
            rv.sys.executable,
            str(rv.RENDER_SCRIPT),
            "--device", "R1",
        ]),
        call([
            rv.sys.executable,
            str(rv.COLLECT_SCRIPT),
            "--device", "R1",
        ]),
        call([
            rv.sys.executable,
            str(rv.VALIDATE_SCRIPT),
            "--device", "R1",
        ]),
        call([
            rv.sys.executable,
            str(rv.DRIFT_SCRIPT),
            "--device", "R1",
        ]),
    ]

    _, report = read_single_report(report_dir)

    assert report["scope"] == "R1"
    assert report["devices"] == [devices[0]]
    assert report["steps"]["collect"]["returncode"] == 0
    assert report["overall_status"] == "pass"


# ------------------------------------------------------------
# Failed validation workflow
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "failing_script",
    [
        "render_config.py",
        "collect_live_configs.py",
        "validate_intent.py",
        "report_drift.py",
    ],
)
def test_main_records_step_failure_and_continues(
    monkeypatch,
    capsys,
    isolated_validation_workflow,
    failing_script,
):
    """Any failed stage must produce a FAIL report without stopping later stages."""

    devices, runner, report_dir = isolated_validation_workflow

    def fake_run_command(command):
        script = Path(command[1]).name

        # For rendering, fail R1 but allow R2 to succeed.
        # For other stages, fail the selected stage.
        should_fail = (
            script == failing_script
            and (
                script != "render_config.py"
                or command[-1] == "R1"
            )
        )

        return {
            "command": [
                command[0],
                script,
                *command[2:],
            ],
            "returncode": 1 if should_fail else 0,
            "stdout": (
                "Simulated failure output"
                if should_fail
                else "Simulated successful operation"
            ),
            "stderr": (
                "Simulated validation error"
                if should_fail
                else ""
            ),
        }

    runner.side_effect = fake_run_command

    monkeypatch.setattr(
        rv.sys,
        "argv",
        ["run_validation.py"],
    )

    with pytest.raises(SystemExit) as exc:
        rv.main()

    assert exc.value.code == 1

    # All five commands must run even when one fails.
    executed_scripts = [
        Path(entry.args[0][1]).name
        for entry in runner.call_args_list
    ]

    assert executed_scripts == [
        "render_config.py",
        "render_config.py",
        "collect_live_configs.py",
        "validate_intent.py",
        "report_drift.py",
    ]

    # R2 rendering must still execute if R1 rendering fails.
    assert runner.call_args_list[1] == call([
        rv.sys.executable,
        str(rv.RENDER_SCRIPT),
        "--device",
        "R2",
    ])

    report_path, report = read_single_report(report_dir)

    assert report_path.is_file()
    assert report["overall_status"] == "fail"
    assert report["scope"] == "all-managed-devices"
    assert len(report["devices"]) == 2

    # Exactly one command result should indicate failure.
    results = [
        *report["steps"]["render"],
        report["steps"]["collect"],
        report["steps"]["intent_validation"],
        report["steps"]["drift"],
    ]

    assert sum(
        result["returncode"] != 0
        for result in results
    ) == 1

    failed_results = [
        result
        for result in results
        if result["returncode"] != 0
    ]

    assert failed_results[0]["command"][1] == failing_script
    assert failed_results[0]["stderr"] == (
        "Simulated validation error"
    )

    output = capsys.readouterr().out

    assert "FAIL" in output
    assert "Simulated validation error" in output
    assert "Overall status: FAIL" in output


# ------------------------------------------------------------
# Unknown managed-device selection
# ------------------------------------------------------------

def test_main_rejects_unknown_device(
    monkeypatch,
    capsys,
    isolated_validation_workflow,
):
    """An unknown device must be rejected before running validation steps."""

    devices, runner, report_dir = isolated_validation_workflow

    monkeypatch.setattr(
        rv.sys,
        "argv",
        [
            "run_validation.py",
            "--device",
            "R999",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        rv.main()

    assert exc.value.code == 1

    assert "Managed device 'R999' was not found" in (
        capsys.readouterr().err
    )

    runner.assert_not_called()

    # No validation report should be created.
    assert not report_dir.exists()
