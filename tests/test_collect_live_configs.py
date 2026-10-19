"""Unit tests for automation.collect_live_configs."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from automation import collect_live_configs as clc
from automation.device_connection import DeviceConnectionError


# ------------------------------------------------------------
# NetBox inventory loading
# ------------------------------------------------------------

def test_load_inventory_success(monkeypatch):
    """Valid JSON returned by the inventory script is parsed."""

    expected = {
        "devices": [
            {
                "hostname": "R1",
                "platform": "Arista EOS",
            }
        ]
    }

    def fake_run(command, **kwargs):
        assert command == [
            clc.sys.executable,
            str(clc.INVENTORY_SCRIPT),
        ]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True

        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(expected),
            stderr="",
        )

    monkeypatch.setattr(clc.subprocess, "run", fake_run)

    assert clc.load_inventory() == expected


@pytest.mark.parametrize(
    "stderr, expected_error",
    [
        (
            "NetBox connection failed",
            "NetBox connection failed",
        ),
        (
            "",
            "Unable to retrieve NetBox inventory.",
        ),
    ],
)
def test_load_inventory_failure(
    monkeypatch, capsys, stderr, expected_error
):
    """Failed inventory subprocesses must produce clear errors."""

    monkeypatch.setattr(
        clc.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=stderr,
        ),
    )

    with pytest.raises(SystemExit) as exc:
        clc.load_inventory()

    assert exc.value.code == 1
    assert expected_error in capsys.readouterr().err


def test_load_inventory_invalid_json(monkeypatch, capsys):
    """Malformed inventory JSON must be rejected."""

    monkeypatch.setattr(
        clc.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="not valid json",
            stderr="",
        ),
    )

    with pytest.raises(SystemExit) as exc:
        clc.load_inventory()

    assert exc.value.code == 1
    assert "Invalid inventory JSON" in capsys.readouterr().err


# ------------------------------------------------------------
# Platform-specific configuration collection
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "platform, expected_command",
    [
        ("Arista EOS", "show running-config"),
        ("Cisco IOS-XE", "show running-config"),
        ("Nokia SR Linux", "info from running /"),
    ],
)
def test_collect_device_success(
    monkeypatch, platform, expected_command
):
    """Each vendor must use its correct collection command."""

    connection = Mock()
    connection.send_command.return_value = (
        "hostname TEST1\n"
        "interface Ethernet1\n\n"
    )

    handler = Mock(return_value=connection)
    monkeypatch.setattr(clc, "open_connection", handler)

    device = {
        "hostname": "TEST1",
        "platform": platform,
        "management_ip": "192.0.2.10/24",
    }

    result = clc.collect_device(device)

    assert result == (
        "hostname TEST1\n"
        "interface Ethernet1\n"
    )

    handler.assert_called_once_with(device)

    connection.send_command.assert_called_once_with(
        expected_command,
        read_timeout=60,
    )

    connection.disconnect.assert_called_once_with()


@pytest.mark.parametrize(
    "empty_output",
    [
        "",
        "  \n\t",
    ],
)
def test_collect_device_empty_response(
    monkeypatch, empty_output
):
    """Empty configurations must be rejected and disconnected."""

    connection = Mock()
    connection.send_command.return_value = empty_output

    monkeypatch.setattr(
        clc,
        "open_connection",
        Mock(return_value=connection),
    )

    device = {
        "hostname": "R1",
        "platform": "Arista EOS",
    }

    with pytest.raises(
        RuntimeError,
        match="Device returned an empty configuration",
    ):
        clc.collect_device(device)

    connection.disconnect.assert_called_once_with()


def test_collect_device_command_failure(monkeypatch):
    """Command failures must still close the connection."""

    connection = Mock()
    connection.send_command.side_effect = RuntimeError(
        "Simulated command failure"
    )

    monkeypatch.setattr(
        clc,
        "open_connection",
        Mock(return_value=connection),
    )

    device = {
        "hostname": "R1",
        "platform": "Arista EOS",
    }

    with pytest.raises(
        RuntimeError,
        match="Simulated command failure",
    ):
        clc.collect_device(device)

    connection.disconnect.assert_called_once_with()


def test_collect_device_unsupported_platform(monkeypatch):
    """Unsupported devices must be rejected before connecting."""

    handler = Mock()
    monkeypatch.setattr(clc, "open_connection", handler)

    device = {
        "hostname": "TEST1",
        "platform": "Unsupported OS",
    }

    with pytest.raises(
        DeviceConnectionError,
        match="Unsupported platform",
    ):
        clc.collect_device(device)

    handler.assert_not_called()


# ------------------------------------------------------------
# Secure configuration file handling
# ------------------------------------------------------------

def test_write_config_secure_permissions(tmp_path):
    """Collected configurations must be stored with private permissions."""
    import stat

    output_dir = tmp_path / "live-configs"
    config = "hostname TEST1\ninterface Ethernet1\n"

    path = clc.write_config(output_dir, "TEST1", config)

    assert path == output_dir / "TEST1.cfg"
    assert path.is_file()
    assert path.read_text() == config

    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ------------------------------------------------------------
# CLI device selection and successful collection
# ------------------------------------------------------------

def test_main_collects_selected_device(
    monkeypatch, tmp_path, capsys
):
    """--device must collect only the requested device."""

    devices = [
        {"hostname": "R1", "platform": "Arista EOS"},
        {"hostname": "R2", "platform": "Cisco IOS-XE"},
    ]

    monkeypatch.setattr(
        clc,
        "load_inventory",
        lambda: {"devices": devices},
    )

    collector = Mock(return_value="hostname R2\n")
    monkeypatch.setattr(clc, "collect_device", collector)

    output_dir = tmp_path / "selected"

    monkeypatch.setattr(
        clc.sys,
        "argv",
        [
            "collect_live_configs.py",
            "--device", "R2",
            "--output-dir", str(output_dir),
        ],
    )

    clc.main()

    collector.assert_called_once_with(devices[1])

    assert (output_dir / "R2.cfg").read_text() == "hostname R2\n"
    assert not (output_dir / "R1.cfg").exists()

    output = capsys.readouterr().out
    assert "PASS  R2" in output
    assert "1 lines" in output


def test_main_rejects_unknown_device(
    monkeypatch, tmp_path, capsys
):
    """An unknown managed device must produce an error."""

    monkeypatch.setattr(
        clc,
        "load_inventory",
        lambda: {
            "devices": [
                {"hostname": "R1", "platform": "Arista EOS"}
            ]
        },
    )

    collector = Mock()
    monkeypatch.setattr(clc, "collect_device", collector)

    monkeypatch.setattr(
        clc.sys,
        "argv",
        [
            "collect_live_configs.py",
            "--device", "R999",
            "--output-dir", str(tmp_path / "unknown"),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        clc.main()

    assert exc.value.code == 1

    assert "Managed device 'R999' was not found" in (
        capsys.readouterr().err
    )

    collector.assert_not_called()


# ------------------------------------------------------------
# Multiple-device collection and error handling
# ------------------------------------------------------------

def test_main_continues_after_device_failure(
    monkeypatch, tmp_path, capsys
):
    """One failed device must not prevent collecting other devices."""

    devices = [
        {"hostname": "R1", "platform": "Arista EOS"},
        {"hostname": "R2", "platform": "Cisco IOS-XE"},
        {"hostname": "R3", "platform": "Nokia SR Linux"},
    ]

    monkeypatch.setattr(
        clc,
        "load_inventory",
        lambda: {"devices": devices},
    )

    def fake_collect(device):
        if device["hostname"] == "R2":
            raise ConnectionError("Simulated SSH failure")

        return f"hostname {device['hostname']}\n"

    collector = Mock(side_effect=fake_collect)
    monkeypatch.setattr(clc, "collect_device", collector)

    output_dir = tmp_path / "multi-device"

    monkeypatch.setattr(
        clc.sys,
        "argv",
        [
            "collect_live_configs.py",
            "--output-dir", str(output_dir),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        clc.main()

    assert exc.value.code == 1

    attempted = [
        call.args[0]["hostname"]
        for call in collector.call_args_list
    ]

    assert attempted == ["R1", "R2", "R3"]

    assert (output_dir / "R1.cfg").is_file()
    assert not (output_dir / "R2.cfg").exists()
    assert (output_dir / "R3.cfg").is_file()

    captured = capsys.readouterr()

    assert "PASS  R1" in captured.out
    assert "PASS  R3" in captured.out
    assert "FAIL  R2" in captured.err
    assert "Simulated SSH failure" in captured.err


def test_main_empty_inventory(
    monkeypatch, tmp_path, capsys
):
    """An empty inventory must finish without collecting anything."""
    import stat

    monkeypatch.setattr(
        clc,
        "load_inventory",
        lambda: {"devices": []},
    )

    collector = Mock()
    monkeypatch.setattr(clc, "collect_device", collector)

    output_dir = tmp_path / "empty"

    monkeypatch.setattr(
        clc.sys,
        "argv",
        [
            "collect_live_configs.py",
            "--output-dir", str(output_dir),
        ],
    )

    clc.main()

    collector.assert_not_called()

    assert output_dir.is_dir()
    assert list(output_dir.iterdir()) == []
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    assert capsys.readouterr().out == ""


# ------------------------------------------------------------
# Missing platform collection command
# ------------------------------------------------------------

def test_collect_device_missing_collection_command(monkeypatch):
    """A known platform without a collection command must be rejected."""

    monkeypatch.delitem(
        clc.COLLECTION_COMMANDS,
        "Nokia SR Linux",
    )

    handler = Mock()
    monkeypatch.setattr(clc, "open_connection", handler)

    device = {
        "hostname": "TEST1",
        "platform": "Nokia SR Linux",
    }

    with pytest.raises(
        ValueError,
        match="Unsupported platform",
    ):
        clc.collect_device(device)

    handler.assert_not_called()
