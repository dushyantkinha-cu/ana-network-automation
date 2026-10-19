"""Unit tests for automation.compare_goldens."""

import json

import pytest

from automation import compare_goldens as cg


@pytest.fixture
def isolated_snapshots(tmp_path, monkeypatch):
    """Keep every test away from the production golden directory."""

    repo = tmp_path / "repository"
    repo.mkdir()

    golden_dir = repo / "golden-configs"
    golden_dir.mkdir()

    monkeypatch.setattr(cg, "REPO_ROOT", repo)
    monkeypatch.setattr(cg, "GOLDEN_DIR", golden_dir)

    return repo, golden_dir


def make_snapshot(parent, name, manifest=None):
    """Create a temporary snapshot, optionally with a manifest."""

    snapshot = parent / name
    snapshot.mkdir(parents=True)

    if manifest is not None:
        (snapshot / "manifest.json").write_text(
            json.dumps(manifest)
        )

    return snapshot


# ------------------------------------------------------------
# Snapshot discovery
# ------------------------------------------------------------

def test_snapshot_dirs_sorted(isolated_snapshots):
    """Only directories containing manifests are valid snapshots."""

    _, golden_dir = isolated_snapshots

    older = make_snapshot(golden_dir, "2026-01", {"devices": []})
    newer = make_snapshot(golden_dir, "2026-03", {"devices": []})

    make_snapshot(golden_dir, "2026-02")

    (golden_dir / "notes.txt").write_text("not a snapshot")

    assert cg.snapshot_dirs() == [older, newer]


# ------------------------------------------------------------
# Snapshot path resolution
# ------------------------------------------------------------

def test_resolve_snapshot_by_id(isolated_snapshots):
    _, golden_dir = isolated_snapshots

    snapshot = make_snapshot(
        golden_dir, "snapshot-001", {"devices": []}
    )

    assert cg.resolve_snapshot("snapshot-001") == snapshot.resolve()


def test_resolve_snapshot_absolute_path(isolated_snapshots):
    _, golden_dir = isolated_snapshots

    snapshot = make_snapshot(
        golden_dir, "snapshot-002", {"devices": []}
    )

    assert cg.resolve_snapshot(str(snapshot)) == snapshot.resolve()


def test_resolve_snapshot_repo_relative(isolated_snapshots):
    repo, _ = isolated_snapshots

    snapshot = make_snapshot(
        repo / "archives",
        "snapshot-003",
        {"devices": []},
    )

    assert cg.resolve_snapshot(
        "archives/snapshot-003"
    ) == snapshot.resolve()


def test_resolve_snapshot_nonexistent(
    isolated_snapshots, capsys
):
    with pytest.raises(SystemExit) as exc:
        cg.resolve_snapshot("does-not-exist")

    assert exc.value.code == 1
    assert "Golden snapshot does not exist" in (
        capsys.readouterr().err
    )


def test_resolve_snapshot_missing_manifest(
    isolated_snapshots, capsys
):
    _, golden_dir = isolated_snapshots

    make_snapshot(golden_dir, "incomplete")

    with pytest.raises(SystemExit) as exc:
        cg.resolve_snapshot("incomplete")

    assert exc.value.code == 1
    assert "Snapshot has no manifest.json" in (
        capsys.readouterr().err
    )


# ------------------------------------------------------------
# Manifest loading
# ------------------------------------------------------------

def test_load_manifest_success(tmp_path):
    manifest = {
        "devices": [
            {"hostname": "R1", "sha256": "abc123"},
            {"hostname": "R2", "sha256": "def456"},
        ]
    }

    snapshot = make_snapshot(
        tmp_path, "valid", manifest
    )

    assert cg.load_manifest(snapshot) == manifest


@pytest.mark.parametrize(
    "manifest_text",
    [
        None,
        "{ invalid json",
    ],
)
def test_load_manifest_failure(
    tmp_path, capsys, manifest_text
):
    snapshot = make_snapshot(tmp_path, "invalid")

    if manifest_text is not None:
        (snapshot / "manifest.json").write_text(
            manifest_text
        )

    with pytest.raises(SystemExit) as exc:
        cg.load_manifest(snapshot)

    assert exc.value.code == 1
    assert "Unable to read" in capsys.readouterr().err


# ------------------------------------------------------------
# Device manifest indexing
# ------------------------------------------------------------

def test_device_map_success():
    manifest = {
        "devices": [
            {"hostname": "R1", "sha256": "oldhash"},
            {"hostname": "R2", "sha256": "newhash"},
        ]
    }

    result = cg.device_map(manifest)

    assert set(result) == {"R1", "R2"}
    assert result["R1"]["sha256"] == "oldhash"
    assert result["R2"]["sha256"] == "newhash"


def test_device_map_empty():
    assert cg.device_map({}) == {}
    assert cg.device_map({"devices": []}) == {}


@pytest.mark.parametrize(
    "hostname",
    [None, ""],
)
def test_device_map_missing_hostname(hostname, capsys):
    manifest = {
        "devices": [
            {"hostname": hostname, "sha256": "abc123"}
        ]
    }

    with pytest.raises(SystemExit) as exc:
        cg.device_map(manifest)

    assert exc.value.code == 1
    assert "without a hostname" in capsys.readouterr().err


# ------------------------------------------------------------
# Reading configuration snapshots
# ------------------------------------------------------------

def test_read_config_success(tmp_path):
    snapshot = make_snapshot(tmp_path, "snapshot-001")

    (snapshot / "R1.cfg").write_text(
        "hostname R1\n"
        "interface Ethernet1\n"
    )

    record = {
        "hostname": "R1",
        "snapshot_file": "R1.cfg",
    }

    assert cg.read_config(snapshot, record) == [
        "hostname R1",
        "interface Ethernet1",
    ]


def test_read_config_missing_filename(tmp_path, capsys):
    snapshot = make_snapshot(tmp_path, "snapshot-001")

    with pytest.raises(SystemExit) as exc:
        cg.read_config(snapshot, {"hostname": "R1"})

    assert exc.value.code == 1
    assert "no snapshot_file in manifest" in (
        capsys.readouterr().err
    )


def test_read_config_missing_file(tmp_path, capsys):
    snapshot = make_snapshot(tmp_path, "snapshot-001")

    record = {
        "hostname": "R1",
        "snapshot_file": "R1.cfg",
    }

    with pytest.raises(SystemExit) as exc:
        cg.read_config(snapshot, record)

    assert exc.value.code == 1
    assert "Golden config missing" in (
        capsys.readouterr().err
    )


# ------------------------------------------------------------
# Unified configuration differences
# ------------------------------------------------------------

def test_show_diff_unified_output(tmp_path, capsys):
    """Changed configuration lines must appear in unified diff format."""

    old = make_snapshot(tmp_path, "older")
    new = make_snapshot(tmp_path, "newer")

    (old / "R1.cfg").write_text(
        "hostname R1\ninterface Ethernet1\n"
    )

    (new / "R1.cfg").write_text(
        "hostname R1\ninterface Ethernet2\n"
    )

    record = {
        "hostname": "R1",
        "snapshot_file": "R1.cfg",
    }

    cg.show_diff("R1", old, record, new, record)

    output = capsys.readouterr().out

    assert "--- older/R1.cfg" in output
    assert "+++ newer/R1.cfg" in output
    assert "-interface Ethernet1" in output
    assert "+interface Ethernet2" in output


# ------------------------------------------------------------
# Default snapshot selection and change classification
# ------------------------------------------------------------

def test_main_default_snapshots_and_change_categories(
    isolated_snapshots, monkeypatch, capsys
):
    """Compare the latest two snapshots and classify every device."""

    _, golden_dir = isolated_snapshots

    make_snapshot(
        golden_dir,
        "2026-01",
        {"devices": []},
    )

    make_snapshot(
        golden_dir,
        "2026-02",
        {
            "devices": [
                {"hostname": "R1", "sha256": "same"},
                {"hostname": "R2", "sha256": "oldhash"},
                {"hostname": "R3", "sha256": "removed"},
            ]
        },
    )

    make_snapshot(
        golden_dir,
        "2026-03",
        {
            "devices": [
                {"hostname": "R1", "sha256": "same"},
                {"hostname": "R2", "sha256": "newhash"},
                {"hostname": "R4", "sha256": "added"},
            ]
        },
    )

    monkeypatch.setattr(
        cg.sys,
        "argv",
        ["compare_goldens.py"],
    )

    cg.main()

    output = capsys.readouterr().out

    assert "From: 2026-02" in output
    assert "To: 2026-03" in output

    assert "UNCHANGED  R1" in output
    assert "CHANGED    R2" in output
    assert "REMOVED    R3" in output
    assert "ADDED      R4" in output

    assert "old: oldhash" in output
    assert "new: newhash" in output

    assert "Changed: 1" in output
    assert "Unchanged: 1" in output
    assert "Added: 1" in output
    assert "Removed: 1" in output

    # Without --details, configuration diffs are not printed.
    assert "--- 2026-02/R2.cfg" not in output


# ------------------------------------------------------------
# Explicit snapshots and --details
# ------------------------------------------------------------

def test_main_explicit_snapshots_with_details(
    isolated_snapshots, monkeypatch, capsys
):
    """Explicit snapshot selection must support configuration diffs."""

    _, golden_dir = isolated_snapshots

    old = make_snapshot(
        golden_dir,
        "older",
        {
            "devices": [
                {
                    "hostname": "R1",
                    "sha256": "oldhash",
                    "snapshot_file": "R1.cfg",
                }
            ]
        },
    )

    new = make_snapshot(
        golden_dir,
        "newer",
        {
            "devices": [
                {
                    "hostname": "R1",
                    "sha256": "newhash",
                    "snapshot_file": "R1.cfg",
                }
            ]
        },
    )

    (old / "R1.cfg").write_text(
        "hostname R1\ninterface Ethernet1\n"
    )

    (new / "R1.cfg").write_text(
        "hostname R1\ninterface Ethernet2\n"
    )

    monkeypatch.setattr(
        cg.sys,
        "argv",
        [
            "compare_goldens.py",
            "--from-snapshot", "older",
            "--to-snapshot", "newer",
            "--details",
        ],
    )

    cg.main()

    output = capsys.readouterr().out

    assert "From: older" in output
    assert "To: newer" in output
    assert "CHANGED    R1" in output

    assert "--- older/R1.cfg" in output
    assert "+++ newer/R1.cfg" in output
    assert "-interface Ethernet1" in output
    assert "+interface Ethernet2" in output

    assert "Changed: 1" in output


# ------------------------------------------------------------
# Insufficient snapshot handling
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "snapshot_count",
    [0, 1],
)
def test_main_requires_two_default_snapshots(
    isolated_snapshots,
    monkeypatch,
    capsys,
    snapshot_count,
):
    """Default comparison requires at least two valid snapshots."""

    _, golden_dir = isolated_snapshots

    for index in range(snapshot_count):
        make_snapshot(
            golden_dir,
            f"snapshot-{index}",
            {"devices": []},
        )

    monkeypatch.setattr(
        cg.sys,
        "argv",
        ["compare_goldens.py"],
    )

    with pytest.raises(SystemExit) as exc:
        cg.main()

    assert exc.value.code == 1

    assert "At least two golden snapshots" in (
        capsys.readouterr().err
    )


def test_main_missing_default_new_snapshot(
    isolated_snapshots, monkeypatch, capsys
):
    """An explicit old snapshot still needs a valid newer snapshot."""

    repo, _ = isolated_snapshots

    archive = repo / "archives"
    archive.mkdir()

    old = make_snapshot(
        archive,
        "older",
        {"devices": []},
    )

    monkeypatch.setattr(
        cg.sys,
        "argv",
        [
            "compare_goldens.py",
            "--from-snapshot", str(old),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cg.main()

    assert exc.value.code == 1
    assert "No golden snapshots were found" in (
        capsys.readouterr().err
    )


# ------------------------------------------------------------
# Empty snapshots
# ------------------------------------------------------------

def test_main_empty_device_lists(
    isolated_snapshots, monkeypatch, capsys
):
    """Valid empty snapshots should produce zero change counts."""

    _, golden_dir = isolated_snapshots

    make_snapshot(golden_dir, "older", {"devices": []})
    make_snapshot(golden_dir, "newer", {"devices": []})

    monkeypatch.setattr(
        cg.sys,
        "argv",
        ["compare_goldens.py"],
    )

    cg.main()

    output = capsys.readouterr().out

    assert "Changed: 0" in output
    assert "Unchanged: 0" in output
    assert "Added: 0" in output
    assert "Removed: 0" in output


# ------------------------------------------------------------
# Explicit older snapshot with automatic newer snapshot
# ------------------------------------------------------------

def test_main_explicit_from_uses_latest_to(
    isolated_snapshots, monkeypatch, capsys
):
    """--from-snapshot alone should use the newest available target."""

    _, golden_dir = isolated_snapshots

    make_snapshot(golden_dir, "2026-01-older", {"devices": []})
    make_snapshot(golden_dir, "2026-02-newer", {"devices": []})

    monkeypatch.setattr(
        cg.sys,
        "argv",
        [
            "compare_goldens.py",
            "--from-snapshot", "2026-01-older",
        ],
    )

    cg.main()

    output = capsys.readouterr().out

    assert "From: 2026-01-older" in output
    assert "To: 2026-02-newer" in output
