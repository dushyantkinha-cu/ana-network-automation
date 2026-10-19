"""Unit tests for automation.validate_intent."""

import pytest

from automation import validate_intent as vi


# ------------------------------------------------------------
# IPv6 and command normalization
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("2001:0db8:0000::1", "2001:db8::1"),
        ("2001:0db8::1/64", "2001:db8::1/64"),
        ("10.0.0.1/31", "10.0.0.1/31"),
        ("Ethernet1", "Ethernet1"),
        ("2001:db8::invalid", "2001:db8::invalid"),
    ],
)
def test_normalize_ipv6_token(value, expected):
    assert vi.normalize_ipv6_token(value) == expected


def test_normalize_command():
    command = "  ipv6   address  2001:0db8::1/64  "

    assert vi.normalize_command(command) == (
        "ipv6 address 2001:db8::1/64"
    )


# ------------------------------------------------------------
# Cisco / Arista configuration parsing
# ------------------------------------------------------------

def test_parse_indented_config():
    config = """\
interface Ethernet1
   description Uplink
   no switchport
   ip address 10.0.0.1/31
!
router ospf 1
   router-id 10.0.0.1
end
"""

    paths = vi.parse_indented_config(config)

    assert ("interface Ethernet1",) in paths
    assert ("interface Ethernet1", "description Uplink") in paths
    assert ("interface Ethernet1", "no switchport") in paths
    assert (
        "interface Ethernet1",
        "ip address 10.0.0.1/31",
    ) in paths

    assert ("router ospf 1", "router-id 10.0.0.1") in paths

    assert ("!",) not in paths
    assert ("end",) not in paths


def test_parse_indented_config_headers():
    config = """\
Building configuration...
Current configuration : 120 bytes
interface Ethernet1
   no switchport
end
"""

    paths = vi.parse_indented_config(config)

    assert ("interface Ethernet1", "no switchport") in paths
    assert len(paths) == 2


# ------------------------------------------------------------
# Nokia SR Linux configuration parsing
# ------------------------------------------------------------

def test_parse_srlinux_config():
    config = """\
interface ethernet-1/1 {
    admin-state enable
    subinterface 0 {
        ipv4 {
            admin-state enable
        }
    }
}
"""

    paths = vi.parse_srlinux_config(config)

    assert ("interface ethernet-1/1",) in paths

    assert (
        "interface ethernet-1/1",
        "admin-state enable",
    ) in paths

    assert (
        "interface ethernet-1/1",
        "subinterface 0",
        "ipv4",
        "admin-state enable",
    ) in paths


def test_parse_srlinux_config_empty():
    assert vi.parse_srlinux_config("") == set()


# ------------------------------------------------------------
# Platform selection and formatting
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "platform",
    ["Arista EOS", "Cisco IOS-XE"],
)
def test_parse_config_indented_platforms(platform):
    config = "interface Ethernet1\n   no switchport\n"

    paths = vi.parse_config(platform, config)

    assert ("interface Ethernet1", "no switchport") in paths


def test_parse_config_nokia():
    config = "interface ethernet-1/1 {\n admin-state enable\n}\n"

    paths = vi.parse_config("Nokia SR Linux", config)

    assert (
        "interface ethernet-1/1",
        "admin-state enable",
    ) in paths


def test_parse_config_unsupported_platform():
    with pytest.raises(ValueError, match="Unsupported platform"):
        vi.parse_config("Unsupported OS", "")


def test_format_path():
    path = ("interface Ethernet1", "no switchport")

    assert vi.format_path(path) == (
        "interface Ethernet1 / no switchport"
    )


# ------------------------------------------------------------
# Device validation using temporary files
# ------------------------------------------------------------

@pytest.fixture
def config_dirs(tmp_path, monkeypatch):
    generated = tmp_path / "generated-configs"
    live = tmp_path / "live-configs"

    generated.mkdir()
    live.mkdir()

    monkeypatch.setattr(vi, "GENERATED_DIR", generated)
    monkeypatch.setattr(vi, "LIVE_DIR", live)

    return generated, live


def test_validate_device_success(config_dirs):
    generated, live = config_dirs

    (generated / "R1.cfg").write_text(
        "interface Ethernet1\n   no switchport\n"
    )

    (live / "R1.cfg").write_text(
        "interface Ethernet1\n"
        "   no switchport\n"
        "   description Extra\n"
    )

    device = {
        "hostname": "R1",
        "platform": "Arista EOS",
    }

    result = vi.validate_device(device)

    assert result["hostname"] == "R1"
    assert result["intended_count"] == 2
    assert result["live_count"] == 3
    assert result["missing"] == []


def test_validate_device_missing_command(config_dirs):
    generated, live = config_dirs

    (generated / "R1.cfg").write_text(
        "interface Ethernet1\n"
        "   no switchport\n"
        "   description Uplink\n"
    )

    (live / "R1.cfg").write_text(
        "interface Ethernet1\n"
        "   no switchport\n"
    )

    device = {
        "hostname": "R1",
        "platform": "Arista EOS",
    }

    result = vi.validate_device(device)

    assert result["missing"] == [
        ("interface Ethernet1", "description Uplink")
    ]


def test_validate_device_missing_generated_file(config_dirs):
    device = {
        "hostname": "R1",
        "platform": "Arista EOS",
    }

    with pytest.raises(
        FileNotFoundError,
        match="Generated configuration missing",
    ):
        vi.validate_device(device)


def test_validate_device_missing_live_file(config_dirs):
    generated, _ = config_dirs

    (generated / "R1.cfg").write_text(
        "interface Ethernet1\n"
    )

    device = {
        "hostname": "R1",
        "platform": "Arista EOS",
    }

    with pytest.raises(
        FileNotFoundError,
        match="Live configuration missing",
    ):
        vi.validate_device(device)


# ------------------------------------------------------------
# NetBox inventory loading (mocked subprocess)
# ------------------------------------------------------------

def test_load_inventory_success(monkeypatch):
    from types import SimpleNamespace

    expected = {
        "devices": [
            {"hostname": "R1", "platform": "Arista EOS"}
        ]
    }

    def fake_run(*args, **kwargs):
        assert args[0][1] == str(vi.INVENTORY_SCRIPT)
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True

        return SimpleNamespace(
            returncode=0,
            stdout='{"devices": [{"hostname": "R1", "platform": "Arista EOS"}]}',
            stderr="",
        )

    monkeypatch.setattr(vi.subprocess, "run", fake_run)

    assert vi.load_inventory() == expected


def test_load_inventory_subprocess_failure(monkeypatch, capsys):
    from types import SimpleNamespace

    monkeypatch.setattr(
        vi.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="NetBox connection failed",
        ),
    )

    with pytest.raises(SystemExit) as exc:
        vi.load_inventory()

    assert exc.value.code == 1
    assert "NetBox connection failed" in capsys.readouterr().err


def test_load_inventory_invalid_json(monkeypatch, capsys):
    from types import SimpleNamespace

    monkeypatch.setattr(
        vi.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="invalid json",
            stderr="",
        ),
    )

    with pytest.raises(SystemExit) as exc:
        vi.load_inventory()

    assert exc.value.code == 1
    assert "Invalid inventory JSON" in capsys.readouterr().err


# ------------------------------------------------------------
# Main CLI workflow tests (mocked)
# ------------------------------------------------------------

def test_main_all_devices_success(monkeypatch, capsys):
    devices = [
        {"hostname": "R1", "platform": "Arista EOS"},
        {"hostname": "R3", "platform": "Cisco IOS-XE"},
    ]

    monkeypatch.setattr(
        vi, "load_inventory", lambda: {"devices": devices}
    )

    def fake_validate(device):
        return {
            "hostname": device["hostname"],
            "platform": device["platform"],
            "intended_count": 5,
            "live_count": 5,
            "missing": [],
        }

    monkeypatch.setattr(vi, "validate_device", fake_validate)
    monkeypatch.setattr(vi.sys, "argv", ["validate_intent.py"])

    vi.main()

    output = capsys.readouterr().out
    assert "PASS  R1" in output
    assert "PASS  R3" in output


def test_main_device_filter(monkeypatch, capsys):
    devices = [
        {"hostname": "R1", "platform": "Arista EOS"},
        {"hostname": "R2", "platform": "Arista EOS"},
    ]

    validated = []

    monkeypatch.setattr(
        vi, "load_inventory", lambda: {"devices": devices}
    )

    def fake_validate(device):
        validated.append(device["hostname"])
        return {
            "hostname": device["hostname"],
            "platform": device["platform"],
            "intended_count": 2,
            "live_count": 2,
            "missing": [],
        }

    monkeypatch.setattr(vi, "validate_device", fake_validate)
    monkeypatch.setattr(
        vi.sys, "argv", ["validate_intent.py", "--device", "R2"]
    )

    vi.main()

    assert validated == ["R2"]
    assert "PASS  R2" in capsys.readouterr().out


@pytest.mark.parametrize("show_missing", [False, True])
def test_main_missing_configuration(
    monkeypatch, capsys, show_missing
):
    device = {
        "hostname": "R1",
        "platform": "Arista EOS",
    }

    monkeypatch.setattr(
        vi, "load_inventory", lambda: {"devices": [device]}
    )

    monkeypatch.setattr(
        vi,
        "validate_device",
        lambda device: {
            "hostname": "R1",
            "platform": "Arista EOS",
            "intended_count": 3,
            "live_count": 2,
            "missing": [
                ("interface Ethernet1", "description Uplink")
            ],
        },
    )

    args = ["validate_intent.py"]

    if show_missing:
        args.append("--show-missing")

    monkeypatch.setattr(vi.sys, "argv", args)

    with pytest.raises(SystemExit) as exc:
        vi.main()

    assert exc.value.code == 1

    output = capsys.readouterr().out
    assert "FAIL  R1" in output
    assert "1 intended paths missing" in output

    missing_path = "interface Ethernet1 / description Uplink"

    if show_missing:
        assert missing_path in output
    else:
        assert missing_path not in output


def test_main_unknown_device(monkeypatch, capsys):
    monkeypatch.setattr(
        vi,
        "load_inventory",
        lambda: {
            "devices": [
                {"hostname": "R1", "platform": "Arista EOS"}
            ]
        },
    )

    monkeypatch.setattr(
        vi.sys,
        "argv",
        ["validate_intent.py", "--device", "R9"],
    )

    with pytest.raises(SystemExit) as exc:
        vi.main()

    assert exc.value.code == 1

    assert "Managed device 'R9' was not found" in (
        capsys.readouterr().err
    )


def test_main_validation_exception(monkeypatch, capsys):
    device = {
        "hostname": "R1",
        "platform": "Arista EOS",
    }

    monkeypatch.setattr(
        vi, "load_inventory", lambda: {"devices": [device]}
    )

    def fake_validate(device):
        raise FileNotFoundError("Simulated missing configuration")

    monkeypatch.setattr(vi, "validate_device", fake_validate)
    monkeypatch.setattr(vi.sys, "argv", ["validate_intent.py"])

    with pytest.raises(SystemExit) as exc:
        vi.main()

    assert exc.value.code == 1

    error = capsys.readouterr().err
    assert "FAIL  R1" in error
    assert "FileNotFoundError" in error
    assert "Simulated missing configuration" in error
