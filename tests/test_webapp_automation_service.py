"""Unit tests for webapp.services.automation."""

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from webapp.services import automation as svc


# ------------------------------------------------------------
# Validation subprocess execution
# ------------------------------------------------------------

@pytest.mark.parametrize("hostname", [None, "R2"])
@pytest.mark.parametrize("exit_code", [0, 3])
def test_run_validation_command(
    monkeypatch,
    tmp_path,
    hostname,
    exit_code,
):
    """Validation must use the expected command and report its result."""

    fake_script = tmp_path / "fake-validation.py"

    monkeypatch.setattr(svc, "VALIDATION_SCRIPT", fake_script)
    monkeypatch.setattr(svc, "REPO_ROOT", tmp_path)

    process_result = SimpleNamespace(
        returncode=exit_code,
        stdout="  Simulated validation output \n",
        stderr="  Simulated diagnostic output \n",
    )

    runner = Mock(return_value=process_result)

    monkeypatch.setattr(
        svc.subprocess,
        "run",
        runner,
    )

    result = svc.run_validation(hostname)

    expected_command = [
        svc.sys.executable,
        str(fake_script),
    ]

    if hostname:
        expected_command.extend([
            "--device",
            hostname,
        ])

    expected_command.append("--details")

    runner.assert_called_once_with(
        expected_command,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=svc.os.environ,
    )

    assert result == {
        "returncode": exit_code,
        "stdout": "Simulated validation output",
        "stderr": "Simulated diagnostic output",
    }


def test_run_validation_subprocess_exception(
    monkeypatch,
):
    """Unexpected subprocess errors must not be silently swallowed."""

    original_error = subprocess.TimeoutExpired(
        cmd=["fake-validation"],
        timeout=10,
    )

    runner = Mock(side_effect=original_error)

    monkeypatch.setattr(
        svc.subprocess,
        "run",
        runner,
    )

    with pytest.raises(subprocess.TimeoutExpired):
        svc.run_validation("R1")

    runner.assert_called_once()


# ------------------------------------------------------------
# Missing validation reports
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "report",
    [
        None,
        {},
    ],
)
def test_validation_step_status_no_report(report):
    """No report must produce an empty status dictionary."""

    assert svc.validation_step_status(report) == {}


# ------------------------------------------------------------
# Rendering status
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "render_results, expected",
    [
        ([{"returncode": 0}], True),
        ([{"returncode": 1}], False),
        (
            [
                {"returncode": 0},
                {"returncode": 2},
            ],
            False,
        ),
        (None, False),
        ([], True),
    ],
)
def test_validation_step_status_render(
    render_results,
    expected,
):
    """Rendering passes only when all listed commands succeed."""

    report = {
        "steps": {
            "render": render_results,
        }
    }

    statuses = svc.validation_step_status(report)

    assert statuses["render"] is expected

    assert statuses["collect"] is False
    assert statuses["intent_validation"] is False
    assert statuses["drift"] is False


# ------------------------------------------------------------
# Collection, intent validation, and drift statuses
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "step_name",
    [
        "collect",
        "intent_validation",
        "drift",
    ],
)
@pytest.mark.parametrize(
    "step_result, expected",
    [
        ({"returncode": 0}, True),
        ({"returncode": 1}, False),
        ({"skipped": True}, None),
        ({"skipped": True, "returncode": 1}, None),
        ("invalid-result", False),
        (None, False),
    ],
)
def test_validation_step_status_individual_step(
    step_name,
    step_result,
    expected,
):
    """Each validation stage must interpret success, failure, and skips."""

    report = {
        "steps": {
            "render": [{"returncode": 0}],
            step_name: step_result,
        }
    }

    statuses = svc.validation_step_status(report)

    assert statuses["render"] is True
    assert statuses[step_name] is expected


# ------------------------------------------------------------
# Complete successful validation report
# ------------------------------------------------------------

def test_validation_step_status_full_success():
    """A successful report must mark every stage as passed."""

    report = {
        "steps": {
            "render": [
                {"device": "R1", "returncode": 0},
                {"device": "R2", "returncode": 0},
            ],
            "collect": {"returncode": 0},
            "intent_validation": {"returncode": 0},
            "drift": {"returncode": 0},
        }
    }

    assert svc.validation_step_status(report) == {
        "render": True,
        "collect": True,
        "intent_validation": True,
        "drift": True,
    }


# ------------------------------------------------------------
# Validation report discovery
# ------------------------------------------------------------

import json
import os
from pathlib import Path


@pytest.mark.parametrize(
    "create_directory",
    [False, True],
)
def test_latest_validation_report_no_matching_reports(
    monkeypatch,
    tmp_path,
    create_directory,
):
    """Missing directories and directories without reports return None."""

    report_dir = tmp_path / "validation-reports"

    if create_directory:
        report_dir.mkdir()
        (report_dir / "unrelated.json").write_text("{}")

    monkeypatch.setattr(
        svc,
        "VALIDATION_DIR",
        report_dir,
    )

    assert svc.latest_validation_report() is None


def test_latest_validation_report_selects_newest(
    monkeypatch,
    tmp_path,
):
    """Reports must be selected by modification time, not filename."""

    report_dir = tmp_path / "validation-reports"
    report_dir.mkdir()

    older_path = report_dir / "validation-z-older.json"
    newer_path = report_dir / "validation-a-newer.json"

    older_data = {
        "overall_status": "fail",
        "scope": "R1",
    }

    newer_data = {
        "overall_status": "pass",
        "scope": "all-managed-devices",
    }

    older_path.write_text(json.dumps(older_data))
    newer_path.write_text(json.dumps(newer_data))

    # Force deterministic timestamps.
    os.utime(older_path, (100, 100))
    os.utime(newer_path, (200, 200))

    monkeypatch.setattr(
        svc,
        "VALIDATION_DIR",
        report_dir,
    )

    result = svc.latest_validation_report()

    assert result == {
        "path": newer_path,
        "name": "validation-a-newer.json",
        "data": newer_data,
    }


def test_latest_validation_report_invalid_json(
    monkeypatch,
    tmp_path,
):
    """A malformed validation report must not crash the service."""

    report_path = tmp_path / "validation-broken.json"
    report_path.write_text("{invalid JSON")

    monkeypatch.setattr(
        svc,
        "VALIDATION_DIR",
        tmp_path,
    )

    assert svc.latest_validation_report() is None


def test_latest_validation_report_read_error(
    monkeypatch,
    tmp_path,
):
    """Report read errors must be handled safely."""

    report_path = tmp_path / "validation-unreadable.json"
    report_path.write_text('{"overall_status": "pass"}')

    monkeypatch.setattr(
        svc,
        "VALIDATION_DIR",
        tmp_path,
    )

    original_open = Path.open

    def simulated_open(path, *args, **kwargs):
        if path == report_path:
            raise OSError("Simulated report read failure")

        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", simulated_open)

    assert svc.latest_validation_report() is None


# ------------------------------------------------------------
# Golden snapshot directory handling
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "create_directory",
    [False, True],
)
def test_golden_snapshots_missing_or_empty(
    monkeypatch,
    tmp_path,
    create_directory,
):
    """No golden directory or an empty directory returns no snapshots."""

    golden_dir = tmp_path / "golden-configs"

    if create_directory:
        golden_dir.mkdir()

    monkeypatch.setattr(
        svc,
        "GOLDEN_DIR",
        golden_dir,
    )

    assert svc.golden_snapshots() == []


# ------------------------------------------------------------
# Valid snapshot discovery and sorting
# ------------------------------------------------------------

def test_golden_snapshots_discovers_and_sorts(
    monkeypatch,
    tmp_path,
):
    """Only valid snapshot directories are returned in descending order."""

    golden_dir = tmp_path / "golden-configs"
    golden_dir.mkdir()

    snapshots = {
        "snapshot-20261001": {
            "devices": ["R1", "R2"],
        },
        "snapshot-20261015": {
            "devices": ["R1", "R2", "S1"],
        },
    }

    for snapshot_id, manifest in snapshots.items():
        snapshot_dir = golden_dir / snapshot_id
        snapshot_dir.mkdir()

        (snapshot_dir / "manifest.json").write_text(
            json.dumps(manifest)
        )

    # Ignore a regular file in the snapshot directory.
    (golden_dir / "unrelated.txt").write_text("ignore")

    # Ignore a directory without a manifest.
    (golden_dir / "snapshot-no-manifest").mkdir()

    # Ignore a directory whose manifest is itself a directory.
    invalid_dir = golden_dir / "snapshot-invalid-manifest"
    invalid_dir.mkdir()
    (invalid_dir / "manifest.json").mkdir()

    monkeypatch.setattr(
        svc,
        "GOLDEN_DIR",
        golden_dir,
    )

    results = svc.golden_snapshots()

    assert [item["snapshot_id"] for item in results] == [
        "snapshot-20261015",
        "snapshot-20261001",
    ]

    for item in results:
        assert item["path"] == golden_dir / item["snapshot_id"]
        assert item["manifest"] == snapshots[item["snapshot_id"]]


# ------------------------------------------------------------
# Invalid manifest handling
# ------------------------------------------------------------

def test_golden_snapshots_skips_invalid_json(
    monkeypatch,
    tmp_path,
):
    """A malformed manifest must be skipped without crashing discovery."""

    golden_dir = tmp_path / "golden-configs"
    snapshot_dir = golden_dir / "snapshot-broken"

    snapshot_dir.mkdir(parents=True)

    (snapshot_dir / "manifest.json").write_text(
        "{broken JSON"
    )

    monkeypatch.setattr(
        svc,
        "GOLDEN_DIR",
        golden_dir,
    )

    assert svc.golden_snapshots() == []


# ------------------------------------------------------------
# Unreadable manifest handling
# ------------------------------------------------------------

def test_golden_snapshots_skips_read_errors(
    monkeypatch,
    tmp_path,
):
    """An unreadable snapshot must not prevent valid snapshots loading."""

    golden_dir = tmp_path / "golden-configs"

    valid_dir = golden_dir / "snapshot-valid"
    unreadable_dir = golden_dir / "snapshot-unreadable"

    valid_dir.mkdir(parents=True)
    unreadable_dir.mkdir()

    valid_manifest = {"devices": ["R1"]}

    (valid_dir / "manifest.json").write_text(
        json.dumps(valid_manifest)
    )

    unreadable_manifest = unreadable_dir / "manifest.json"
    unreadable_manifest.write_text("{}")

    monkeypatch.setattr(
        svc,
        "GOLDEN_DIR",
        golden_dir,
    )

    original_open = Path.open

    def simulated_open(path, *args, **kwargs):
        if path == unreadable_manifest:
            raise OSError("Simulated manifest read failure")

        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", simulated_open)

    results = svc.golden_snapshots()

    assert results == [
        {
            "snapshot_id": "snapshot-valid",
            "path": valid_dir,
            "manifest": valid_manifest,
        }
    ]
