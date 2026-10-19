"""Unit tests for automation.promote_golden."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from automation import promote_golden as pg


MANAGED_HOSTNAMES = [
    "R1", "R2", "R3", "R4",
    "S1", "S2", "S3", "S4",
]


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Redirect promotion paths to temporary test directories."""

    repo = tmp_path / "repository"
    repo.mkdir()

    generated = repo / "generated-configs"
    reports = repo / "validation-reports"
    golden = repo / "golden-configs"

    generated.mkdir()
    reports.mkdir()
    golden.mkdir()

    monkeypatch.setattr(pg, "REPO_ROOT", repo)
    monkeypatch.setattr(pg, "GENERATED_DIR", generated)
    monkeypatch.setattr(pg, "REPORT_DIR", reports)
    monkeypatch.setattr(pg, "GOLDEN_DIR", golden)

    return {
        "repo": repo,
        "generated": generated,
        "reports": reports,
        "golden": golden,
    }


@pytest.fixture
def passing_report():
    """A simulated successful validation of all managed devices."""

    return {
        "overall_status": "pass",
        "scope": "all-managed-devices",
        "timestamp_utc": "2026-10-15T12:00:00Z",
        "devices": [
            {
                "hostname": hostname,
                "platform": "Arista EOS",
                "management_ip": "192.0.2.10/24",
            }
            for hostname in MANAGED_HOSTNAMES
        ],
    }


# ------------------------------------------------------------
# Discovering validation reports
# ------------------------------------------------------------

def test_latest_validation_report_selects_newest(isolated_paths):
    """Report selection must use modification time."""

    report_dir = isolated_paths["reports"]

    older = report_dir / "validation-older.json"
    newer = report_dir / "validation-newer.json"

    older.write_text("{}")
    newer.write_text("{}")

    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    # Files not matching validation-*.json must be ignored.
    unrelated = report_dir / "other.json"
    unrelated.write_text("{}")
    os.utime(unrelated, (3000, 3000))

    assert pg.latest_validation_report() == newer


def test_latest_validation_report_missing(
    isolated_paths, capsys
):
    """Missing reports must prevent promotion."""

    with pytest.raises(SystemExit) as exc:
        pg.latest_validation_report()

    assert exc.value.code == 1
    assert "No validation reports were found" in (
        capsys.readouterr().err
    )


# ------------------------------------------------------------
# Reading validation reports
# ------------------------------------------------------------

def test_load_report_success(
    isolated_paths, passing_report
):
    report_path = (
        isolated_paths["reports"]
        / "validation-all-test.json"
    )

    report_path.write_text(json.dumps(passing_report))

    assert pg.load_report(report_path) == passing_report


@pytest.mark.parametrize(
    "failure",
    ["missing", "invalid_json"],
)
def test_load_report_failure(
    isolated_paths, capsys, failure
):
    report_path = (
        isolated_paths["reports"]
        / "validation-all-bad.json"
    )

    if failure == "invalid_json":
        report_path.write_text("{invalid json")

    with pytest.raises(SystemExit) as exc:
        pg.load_report(report_path)

    assert exc.value.code == 1
    assert "Unable to read validation report" in (
        capsys.readouterr().err
    )


# ------------------------------------------------------------
# SHA-256 integrity hashing
# ------------------------------------------------------------

def test_sha256_file(tmp_path):
    """File hashing must work even for files larger than one chunk."""

    content = b"unit-test-configuration\n" * 50000
    path = tmp_path / "test.cfg"
    path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()

    assert pg.sha256_file(path) == expected


# ------------------------------------------------------------
# Snapshot ID validation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "filename, expected",
    [
        (
            "validation-all-20261015T120000Z.json",
            "20261015T120000Z",
        ),
        (
            "validation-all-2026-10-15_120000.json",
            "2026-10-15_120000",
        ),
    ],
)
def test_report_snapshot_id_valid(filename, expected):
    assert pg.report_snapshot_id(Path(filename)) == expected


@pytest.mark.parametrize(
    "filename, expected_error",
    [
        (
            "validation-R1-20261015.json",
            "all-managed-devices validation report",
        ),
        (
            "validation-all-.json",
            "Unable to derive snapshot timestamp",
        ),
    ],
)
def test_report_snapshot_id_invalid(
    filename, expected_error, capsys
):
    with pytest.raises(SystemExit) as exc:
        pg.report_snapshot_id(Path(filename))

    assert exc.value.code == 1
    assert expected_error in capsys.readouterr().err


# ------------------------------------------------------------
# Successful validation report
# ------------------------------------------------------------

def test_validate_report_success(passing_report):
    """A passing report with exactly eight managed devices is valid."""

    devices = pg.validate_report(
        passing_report,
        Path("validation-all-test.json"),
    )

    assert devices == passing_report["devices"]
    assert {d["hostname"] for d in devices} == set(
        MANAGED_HOSTNAMES
    )


# ------------------------------------------------------------
# Invalid validation reports
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "overrides, expected_error",
    [
        (
            {"overall_status": "fail"},
            "overall_status is not 'pass'",
        ),
        (
            {"overall_status": None},
            "overall_status is not 'pass'",
        ),
        (
            {"scope": "single-device"},
            "all-managed-devices",
        ),
        (
            {"scope": None},
            "all-managed-devices",
        ),
        (
            {"devices": []},
            "contains no managed devices",
        ),
        (
            {"devices": None},
            "contains no managed devices",
        ),
    ],
)
def test_validate_report_rejects_invalid_metadata(
    passing_report, overrides, expected_error, capsys
):
    """Invalid status, scope, or empty inventory must fail."""

    passing_report.update(overrides)

    with pytest.raises(SystemExit) as exc:
        pg.validate_report(
            passing_report,
            Path("validation-all-test.json"),
        )

    assert exc.value.code == 1
    assert expected_error in capsys.readouterr().err


@pytest.mark.parametrize(
    "hostname",
    [None, ""],
)
def test_validate_report_rejects_missing_hostname(
    passing_report, hostname, capsys
):
    """Every device record must have a hostname."""

    passing_report["devices"][0]["hostname"] = hostname

    with pytest.raises(SystemExit) as exc:
        pg.validate_report(
            passing_report,
            Path("validation-all-test.json"),
        )

    assert exc.value.code == 1
    assert "without a hostname" in capsys.readouterr().err


def test_validate_report_rejects_duplicates(
    passing_report, capsys
):
    """Duplicate managed-device records must fail."""

    passing_report["devices"].append(
        dict(passing_report["devices"][0])
    )

    with pytest.raises(SystemExit) as exc:
        pg.validate_report(
            passing_report,
            Path("validation-all-test.json"),
        )

    assert exc.value.code == 1
    assert "duplicate device hostnames" in (
        capsys.readouterr().err
    )


def test_validate_report_rejects_unmanaged_replacement(
    passing_report, capsys
):
    """R5 must not substitute for a required managed device."""

    passing_report["devices"][-1]["hostname"] = "R5"

    with pytest.raises(SystemExit) as exc:
        pg.validate_report(
            passing_report,
            Path("validation-all-test.json"),
        )

    assert exc.value.code == 1
    assert "eight organization-managed devices" in (
        capsys.readouterr().err
    )


def test_validate_report_rejects_extra_device(
    passing_report, capsys
):
    """An additional unmanaged device must invalidate promotion."""

    passing_report["devices"].append(
        {"hostname": "R5", "platform": "Cisco IOS-XE"}
    )

    with pytest.raises(SystemExit) as exc:
        pg.validate_report(
            passing_report,
            Path("validation-all-test.json"),
        )

    assert exc.value.code == 1
    assert "eight organization-managed devices" in (
        capsys.readouterr().err
    )


# ------------------------------------------------------------
# Generated configuration integrity
# ------------------------------------------------------------

def test_validate_generated_configs_success(
    isolated_paths, passing_report
):
    """All eight generated configs must produce valid hash records."""

    generated_dir = isolated_paths["generated"]

    for hostname in MANAGED_HOSTNAMES:
        (generated_dir / f"{hostname}.cfg").write_text(
            f"hostname {hostname}\ninterface Ethernet1\n"
        )

    records = pg.validate_generated_configs(
        passing_report["devices"]
    )

    assert len(records) == 8

    for record in records:
        hostname = record["hostname"]
        source = generated_dir / f"{hostname}.cfg"

        assert record["platform"] == "Arista EOS"
        assert record["management_ip"] == "192.0.2.10/24"
        assert record["source_path"] == (
            f"generated-configs/{hostname}.cfg"
        )
        assert record["snapshot_file"] == f"{hostname}.cfg"
        assert record["size_bytes"] == source.stat().st_size
        assert record["sha256"] == hashlib.sha256(
            source.read_bytes()
        ).hexdigest()
        assert record["_source"] == source

    assert not list(isolated_paths["golden"].iterdir())


def test_validate_generated_configs_missing_file(
    isolated_paths, passing_report, capsys
):
    """Missing generated configurations must block promotion."""

    with pytest.raises(SystemExit) as exc:
        pg.validate_generated_configs(
            passing_report["devices"]
        )

    assert exc.value.code == 1
    assert "Generated configuration missing" in (
        capsys.readouterr().err
    )


def test_validate_generated_configs_empty_file(
    isolated_paths, passing_report, capsys
):
    """An empty generated configuration must block promotion."""

    (isolated_paths["generated"] / "R1.cfg").write_text("")

    with pytest.raises(SystemExit) as exc:
        pg.validate_generated_configs(
            passing_report["devices"]
        )

    assert exc.value.code == 1
    assert "Generated configuration is empty" in (
        capsys.readouterr().err
    )


# ------------------------------------------------------------
# Mocked Git command execution
# ------------------------------------------------------------

def test_git_output_success(isolated_paths, monkeypatch):
    """Git command output should be returned without whitespace."""

    from types import SimpleNamespace
    from unittest.mock import Mock

    runner = Mock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout="abc123\n",
            stderr="",
        )
    )

    monkeypatch.setattr(pg.subprocess, "run", runner)

    assert pg.git_output("rev-parse", "HEAD") == "abc123"

    runner.assert_called_once_with(
        ["git", "rev-parse", "HEAD"],
        cwd=isolated_paths["repo"],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "stderr, expected_error",
    [
        ("fatal: simulated Git failure", "simulated Git failure"),
        ("", "Git command failed."),
    ],
)
def test_git_output_failure(
    isolated_paths, monkeypatch, capsys, stderr, expected_error
):
    """Git failures must stop promotion with a clear error."""

    from types import SimpleNamespace
    from unittest.mock import Mock

    monkeypatch.setattr(
        pg.subprocess,
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
        pg.git_output("status", "--porcelain")

    assert exc.value.code == 1
    assert expected_error in capsys.readouterr().err


# ------------------------------------------------------------
# Git working-tree safety checks
# ------------------------------------------------------------

def test_ensure_clean_git_success(monkeypatch):
    """A clean Git working tree must pass validation."""

    from unittest.mock import Mock

    git = Mock(return_value="")
    monkeypatch.setattr(pg, "git_output", git)

    pg.ensure_clean_git()

    git.assert_called_once_with("status", "--porcelain")


def test_ensure_clean_git_rejects_untracked_files(
    monkeypatch, capsys
):
    """Even untracked files must cause the clean-tree check to fail."""

    from unittest.mock import Mock

    git = Mock(
        return_value="?? tests/test_example.py"
    )

    monkeypatch.setattr(pg, "git_output", git)

    with pytest.raises(SystemExit) as exc:
        pg.ensure_clean_git()

    assert exc.value.code == 1
    assert "Git working tree is not clean" in (
        capsys.readouterr().err
    )

    git.assert_called_once_with("status", "--porcelain")


# ------------------------------------------------------------
# Complete golden promotion workflow
# ------------------------------------------------------------

from unittest.mock import Mock, call


def prepare_promotion_candidate(paths, report):
    """Create an isolated, valid eight-device promotion candidate."""

    report_path = (
        paths["reports"]
        / "validation-all-20261015T120000Z.json"
    )

    report_path.write_text(json.dumps(report))

    for hostname in MANAGED_HOSTNAMES:
        (paths["generated"] / f"{hostname}.cfg").write_text(
            f"hostname {hostname}\ninterface Ethernet1\n"
        )

    return report_path


def test_main_check_only(
    isolated_paths, passing_report, monkeypatch, capsys
):
    """Check-only must not create snapshots or execute Git checks."""

    report_path = prepare_promotion_candidate(
        isolated_paths, passing_report
    )

    git = Mock(side_effect=AssertionError("Git must not run"))
    monkeypatch.setattr(pg, "git_output", git)

    clean_check = Mock(
        side_effect=AssertionError("Clean-tree check must not run")
    )
    monkeypatch.setattr(pg, "ensure_clean_git", clean_check)

    monkeypatch.setattr(
        pg.sys,
        "argv",
        [
            "promote_golden.py",
            "--report", str(report_path),
            "--check-only",
        ],
    )

    pg.main()

    output = capsys.readouterr().out

    assert "CHECK PASS" in output
    assert "20261015T120000Z" in output
    assert "Configuration hashes:" in output

    assert list(isolated_paths["golden"].iterdir()) == []
    clean_check.assert_not_called()
    git.assert_not_called()


def test_main_check_only_uses_latest_report(
    isolated_paths, passing_report, monkeypatch, capsys
):
    """Without --report, the newest available report is selected."""

    newer = prepare_promotion_candidate(
        isolated_paths, passing_report
    )

    older = (
        isolated_paths["reports"]
        / "validation-all-20261014T120000Z.json"
    )
    older.write_text(json.dumps(passing_report))

    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    git = Mock(side_effect=AssertionError("Git must not run"))
    monkeypatch.setattr(pg, "git_output", git)

    monkeypatch.setattr(
        pg.sys,
        "argv",
        ["promote_golden.py", "--check-only"],
    )

    pg.main()

    output = capsys.readouterr().out

    assert f"Validation report: {newer}" in output
    assert "Snapshot ID: 20261015T120000Z" in output
    assert "CHECK PASS" in output

    assert list(isolated_paths["golden"].iterdir()) == []
    git.assert_not_called()


def test_main_successful_promotion(
    isolated_paths, passing_report, monkeypatch, capsys
):
    """Promotion must copy all configs and create a correct manifest."""

    report_path = prepare_promotion_candidate(
        isolated_paths, passing_report
    )

    git_results = {
        ("status", "--porcelain"): "",
        ("rev-parse", "HEAD"): "abc123def456",
        ("branch", "--show-current"): "unit-test-branch",
    }

    git = Mock(side_effect=lambda *args: git_results[args])
    monkeypatch.setattr(pg, "git_output", git)

    monkeypatch.setattr(
        pg.sys,
        "argv",
        ["promote_golden.py", "--report", str(report_path)],
    )

    pg.main()

    snapshot = (
        isolated_paths["golden"]
        / "20261015T120000Z"
    )

    assert snapshot.is_dir()

    # All eight device configurations must be copied.
    for hostname in MANAGED_HOSTNAMES:
        source = isolated_paths["generated"] / f"{hostname}.cfg"
        destination = snapshot / f"{hostname}.cfg"

        assert destination.is_file()
        assert destination.read_bytes() == source.read_bytes()

    # The validation report must be preserved.
    report_copy = snapshot / "validation-report.json"

    assert report_copy.is_file()
    assert report_copy.read_bytes() == report_path.read_bytes()

    # Verify the generated manifest.
    manifest_path = snapshot / "manifest.json"
    assert manifest_path.is_file()

    manifest = json.loads(manifest_path.read_text())

    assert manifest["schema_version"] == 1
    assert manifest["snapshot_id"] == "20261015T120000Z"
    assert manifest["created_at_utc"]

    assert manifest["validation"]["scope"] == (
        "all-managed-devices"
    )
    assert manifest["validation"]["overall_status"] == "pass"
    assert manifest["validation"]["source_report"] == (
        report_path.name
    )
    assert manifest["validation"]["snapshot_report"] == (
        "validation-report.json"
    )
    assert manifest["validation"]["report_sha256"] == (
        hashlib.sha256(report_path.read_bytes()).hexdigest()
    )

    assert manifest["git"]["commit"] == "abc123def456"
    assert manifest["git"]["branch"] == "unit-test-branch"

    assert len(manifest["devices"]) == 8
    assert {
        device["hostname"]
        for device in manifest["devices"]
    } == set(MANAGED_HOSTNAMES)

    for device in manifest["devices"]:
        hostname = device["hostname"]
        config_path = snapshot / f"{hostname}.cfg"

        assert "_source" not in device
        assert device["snapshot_file"] == f"{hostname}.cfg"
        assert device["size_bytes"] == config_path.stat().st_size
        assert device["sha256"] == (
            hashlib.sha256(config_path.read_bytes()).hexdigest()
        )

    assert sorted(p.name for p in snapshot.iterdir()) == sorted(
        [f"{hostname}.cfg" for hostname in MANAGED_HOSTNAMES]
        + ["validation-report.json", "manifest.json"]
    )

    assert git.call_args_list == [
        call("status", "--porcelain"),
        call("rev-parse", "HEAD"),
        call("branch", "--show-current"),
    ]

    output = capsys.readouterr().out
    assert "Golden snapshot created:" in output


def test_main_rejects_existing_snapshot(
    isolated_paths, passing_report, monkeypatch, capsys
):
    """An existing golden snapshot must never be overwritten."""

    report_path = prepare_promotion_candidate(
        isolated_paths, passing_report
    )

    snapshot = isolated_paths["golden"] / "20261015T120000Z"
    snapshot.mkdir()

    marker = snapshot / "existing-file.txt"
    marker.write_text("preserve this file")

    git = Mock(return_value="")
    monkeypatch.setattr(pg, "git_output", git)

    monkeypatch.setattr(
        pg.sys,
        "argv",
        ["promote_golden.py", "--report", str(report_path)],
    )

    with pytest.raises(SystemExit) as exc:
        pg.main()

    assert exc.value.code == 1
    assert "Golden snapshot already exists" in (
        capsys.readouterr().err
    )

    assert marker.read_text() == "preserve this file"
    assert list(snapshot.iterdir()) == [marker]

    git.assert_called_once_with("status", "--porcelain")


def test_main_rejects_dirty_git(
    isolated_paths, passing_report, monkeypatch, capsys
):
    """Uncommitted or untracked changes must prevent promotion."""

    report_path = prepare_promotion_candidate(
        isolated_paths, passing_report
    )

    git = Mock(return_value="?? simulated-untracked-file.txt")
    monkeypatch.setattr(pg, "git_output", git)

    monkeypatch.setattr(
        pg.sys,
        "argv",
        ["promote_golden.py", "--report", str(report_path)],
    )

    with pytest.raises(SystemExit) as exc:
        pg.main()

    assert exc.value.code == 1
    assert "Git working tree is not clean" in (
        capsys.readouterr().err
    )

    assert list(isolated_paths["golden"].iterdir()) == []
    git.assert_called_once_with("status", "--porcelain")


def test_main_rejects_nonexistent_report(
    isolated_paths, monkeypatch, capsys
):
    """A nonexistent validation report must stop promotion."""

    missing = (
        isolated_paths["reports"]
        / "validation-all-missing.json"
    )

    git = Mock(side_effect=AssertionError("Git must not run"))
    monkeypatch.setattr(pg, "git_output", git)

    monkeypatch.setattr(
        pg.sys,
        "argv",
        ["promote_golden.py", "--report", str(missing)],
    )

    with pytest.raises(SystemExit) as exc:
        pg.main()

    assert exc.value.code == 1
    assert "Validation report does not exist" in (
        capsys.readouterr().err
    )

    assert list(isolated_paths["golden"].iterdir()) == []
    git.assert_not_called()


def test_main_rejects_failed_validation_before_git(
    isolated_paths, passing_report, monkeypatch, capsys
):
    """A failed validation must be rejected before any Git operation."""

    passing_report["overall_status"] = "fail"

    report_path = prepare_promotion_candidate(
        isolated_paths, passing_report
    )

    git = Mock(side_effect=AssertionError("Git must not run"))
    monkeypatch.setattr(pg, "git_output", git)

    monkeypatch.setattr(
        pg.sys,
        "argv",
        ["promote_golden.py", "--report", str(report_path)],
    )

    with pytest.raises(SystemExit) as exc:
        pg.main()

    assert exc.value.code == 1
    assert "overall_status is not 'pass'" in (
        capsys.readouterr().err
    )

    assert list(isolated_paths["golden"].iterdir()) == []
    git.assert_not_called()
