"""Unit tests for automation.report_drift."""

import sys
from unittest.mock import patch

import pytest

from automation import validate_intent as vi

# report_drift.py uses a script-style import:
# "from validate_intent import ..."
#
# Make that import resolvable during package-based unit testing
# without changing the production module or sys.path.
with patch.dict(sys.modules, {"validate_intent": vi}):
    from automation import report_drift as rd


# ------------------------------------------------------------
# Sensitive configuration filtering
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "path, expected",
    [
        ((), False),
        (("interface Ethernet1", "description uplink"), False),
        (("system", "password encrypted"), True),
        (("system", "secret abc123"), True),
        (("snmp", "community public"), True),
        (("routing", "authentication-key value"), True),
        (("routing", "privacy-key value"), True),
        (("system", "PRIVATE-KEY value"), True),
    ],
)
def test_contains_sensitive_data(path, expected):
    assert rd.contains_sensitive_data(path) is expected


# ------------------------------------------------------------
# Determining template-managed configuration roots
# ------------------------------------------------------------

def test_intended_roots():
    paths = {
        ("interface Ethernet1", "description uplink"),
        ("interface Ethernet1", "shutdown"),
        ("router ospf 1", "network 10.0.0.0"),
        (),
    }

    assert rd.intended_roots(paths) == {
        "interface Ethernet1",
        "router ospf 1",
    }


# ------------------------------------------------------------
# Nokia SR Linux ownership rules
# ------------------------------------------------------------

SRL_INTENDED = {
    (
        "interface ethernet-1/1",
        "admin-state enable",
    ),
    (
        "network-instance default",
        "interface ethernet-1/1.0",
    ),
    (
        "network-instance default",
        "protocols",
        "ospf",
        "instance main",
        "area 0.0.0.0",
    ),
}


@pytest.mark.parametrize(
    "path, expected",
    [
        (
            (),
            False,
        ),
        (
            ("interface ethernet-1/1", "admin-state enable"),
            True,
        ),
        (
            ("interface ethernet-1/99", "admin-state enable"),
            False,
        ),
        (
            ("network-instance blue", "interface ethernet-1/1.0"),
            False,
        ),
        (
            (
                "network-instance default",
                "interface ethernet-1/1.0",
                "admin-state enable",
            ),
            True,
        ),
        (
            (
                "network-instance default",
                "interface ethernet-1/99.0",
            ),
            False,
        ),
        (
            (
                "network-instance default",
                "protocols",
                "ospf",
                "instance main",
                "area 0.0.0.1",
            ),
            True,
        ),
        (
            (
                "network-instance default",
                "protocols",
                "ospf",
                "instance other",
            ),
            False,
        ),
        (
            ("network-instance default",),
            True,
        ),
        (
            ("network-instance default", "protocols"),
            True,
        ),
        (
            ("network-instance default", "protocols", "ospf"),
            True,
        ),
        (
            ("network-instance default", "protocols", "bgp"),
            False,
        ),
        (
            ("network-instance default", "unsupported"),
            False,
        ),
    ],
)
def test_srl_owned_path(path, expected):
    assert rd.srl_owned_path(path, SRL_INTENDED) is expected


# ------------------------------------------------------------
# Cisco and Arista ownership rules
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "platform",
    ["Arista EOS", "Cisco IOS-XE"],
)
@pytest.mark.parametrize(
    "path, expected",
    [
        ((), False),
        (
            ("interface Ethernet1", "description changed"),
            True,
        ),
        (
            ("interface Ethernet99", "description unmanaged"),
            False,
        ),
        (
            ("router ospf 1", "network 10.1.0.0"),
            True,
        ),
    ],
)
def test_owned_live_path_ios_arista(platform, path, expected):
    intended = {
        ("interface Ethernet1", "description original"),
        ("router ospf 1", "network 10.0.0.0"),
    }

    assert rd.owned_live_path(
        platform, path, intended
    ) is expected


# ------------------------------------------------------------
# Platform dispatch and rejection
# ------------------------------------------------------------

def test_owned_live_path_nokia():
    path = (
        "interface ethernet-1/1",
        "admin-state disable",
    )

    assert rd.owned_live_path(
        "Nokia SR Linux",
        path,
        SRL_INTENDED,
    ) is True


def test_owned_live_path_unsupported_platform():
    with pytest.raises(
        ValueError,
        match="Unsupported platform",
    ):
        rd.owned_live_path(
            "Unsupported OS",
            ("interface Ethernet1",),
            set(),
        )


# ------------------------------------------------------------
# Detailed drift output formatting
# ------------------------------------------------------------

def test_print_paths_empty(capsys):
    rd.print_paths("Missing intended paths", [])

    assert capsys.readouterr().out == ""


def test_print_paths_nonempty(capsys):
    paths = [
        ("interface Ethernet1", "description uplink"),
    ]

    rd.print_paths("Missing intended paths", paths)

    output = capsys.readouterr().out

    assert "Missing intended paths:" in output
    assert rd.format_path(paths[0]) in output


# ------------------------------------------------------------
# Comparing generated and live configurations
# ------------------------------------------------------------

from unittest.mock import Mock, call


@pytest.fixture
def isolated_drift_files(tmp_path, monkeypatch):
    """Use temporary directories instead of real configuration files."""

    generated = tmp_path / "generated-configs"
    live = tmp_path / "live-configs"

    generated.mkdir()
    live.mkdir()

    monkeypatch.setattr(rd, "GENERATED_DIR", generated)
    monkeypatch.setattr(rd, "LIVE_DIR", live)

    return generated, live


def prepare_comparison(
    monkeypatch, directories, intended, live_paths
):
    """Prepare temporary files and simulated parsed configurations."""

    generated_dir, live_dir = directories

    (generated_dir / "TEST1.cfg").write_text("INTENDED\n")
    (live_dir / "TEST1.cfg").write_text("LIVE\n")

    def fake_parse(platform, text):
        if text == "INTENDED\n":
            return intended

        if text == "LIVE\n":
            return live_paths

        raise AssertionError(f"Unexpected config input: {text!r}")

    parser = Mock(side_effect=fake_parse)
    monkeypatch.setattr(rd, "parse_config", parser)

    return parser


# ------------------------------------------------------------
# No configuration drift
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "platform, path",
    [
        (
            "Arista EOS",
            ("interface Ethernet1", "description uplink"),
        ),
        (
            "Cisco IOS-XE",
            ("interface GigabitEthernet1", "description uplink"),
        ),
        (
            "Nokia SR Linux",
            ("interface ethernet-1/1", "admin-state enable"),
        ),
    ],
)
def test_compare_device_no_drift(
    monkeypatch, isolated_drift_files, platform, path
):
    """Identical managed configuration must produce no drift."""

    paths = {path}

    parser = prepare_comparison(
        monkeypatch,
        isolated_drift_files,
        paths,
        paths,
    )

    device = {
        "hostname": "TEST1",
        "platform": platform,
    }

    result = rd.compare_device(device)

    assert result == {
        "hostname": "TEST1",
        "platform": platform,
        "missing": [],
        "extra": [],
    }

    assert parser.call_args_list == [
        call(platform, "INTENDED\n"),
        call(platform, "LIVE\n"),
    ]


# ------------------------------------------------------------
# Cisco and Arista scoped drift
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "platform",
    ["Arista EOS", "Cisco IOS-XE"],
)
def test_compare_device_detects_scoped_drift(
    monkeypatch, isolated_drift_files, platform
):
    """Only differences within managed roots should be reported."""

    intended = {
        ("interface Ethernet1", "description intended"),
        ("interface Ethernet1", "shutdown"),
        ("router ospf 1", "network 10.0.0.0"),
    }

    live = {
        ("interface Ethernet1", "description modified"),
        ("interface Ethernet1", "shutdown"),
        ("router ospf 1", "network 10.0.0.0"),

        # Unmanaged interface: should be ignored.
        ("interface Ethernet99", "description unmanaged"),

        # Unmanaged routing process: should be ignored.
        ("router bgp 65000", "neighbor 192.0.2.1"),

        # Sensitive additional configuration: should be filtered.
        ("interface Ethernet1", "password hidden-value"),
    }

    prepare_comparison(
        monkeypatch,
        isolated_drift_files,
        intended,
        live,
    )

    result = rd.compare_device({
        "hostname": "TEST1",
        "platform": platform,
    })

    assert result["missing"] == [
        ("interface Ethernet1", "description intended"),
    ]

    assert result["extra"] == [
        ("interface Ethernet1", "description modified"),
    ]


# ------------------------------------------------------------
# Nokia SR Linux scoped drift
# ------------------------------------------------------------

def test_compare_device_nokia_scoped_drift(
    monkeypatch, isolated_drift_files
):
    """Nokia drift must respect managed interface and OSPF boundaries."""

    common = {
        ("network-instance default",),
        ("network-instance default", "protocols"),
        ("network-instance default", "protocols", "ospf"),
        (
            "network-instance default",
            "protocols",
            "ospf",
            "instance main",
        ),
        (
            "network-instance default",
            "interface ethernet-1/1.0",
        ),
    }

    intended = common | {
        ("interface ethernet-1/1", "admin-state enable"),
        (
            "network-instance default",
            "protocols",
            "ospf",
            "instance main",
            "area 0.0.0.0",
        ),
    }

    live = common | {
        ("interface ethernet-1/1", "admin-state disable"),
        (
            "network-instance default",
            "protocols",
            "ospf",
            "instance main",
            "area 0.0.0.0",
        ),

        # This interface is outside the managed scope.
        ("interface ethernet-1/99", "admin-state enable"),

        # Unmanaged network-instance attachment.
        (
            "network-instance default",
            "interface ethernet-1/99.0",
        ),

        # Different OSPF instance: not managed.
        (
            "network-instance default",
            "protocols",
            "ospf",
            "instance other",
            "area 0.0.0.1",
        ),

        # BGP is outside the managed OSPF hierarchy.
        (
            "network-instance default",
            "protocols",
            "bgp",
            "neighbor 192.0.2.1",
        ),
    }

    prepare_comparison(
        monkeypatch,
        isolated_drift_files,
        intended,
        live,
    )

    result = rd.compare_device({
        "hostname": "TEST1",
        "platform": "Nokia SR Linux",
    })

    assert result["missing"] == [
        ("interface ethernet-1/1", "admin-state enable"),
    ]

    assert result["extra"] == [
        ("interface ethernet-1/1", "admin-state disable"),
    ]


# ------------------------------------------------------------
# Missing configuration files
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "missing, expected_error",
    [
        ("generated", "Generated configuration missing"),
        ("live", "Live configuration missing"),
    ],
)
def test_compare_device_missing_config(
    monkeypatch,
    isolated_drift_files,
    missing,
    expected_error,
):
    """Missing configuration files must produce clear errors."""

    generated_dir, live_dir = isolated_drift_files

    if missing != "generated":
        (generated_dir / "TEST1.cfg").write_text("INTENDED\n")

    if missing != "live":
        (live_dir / "TEST1.cfg").write_text("LIVE\n")

    parser = Mock()
    monkeypatch.setattr(rd, "parse_config", parser)

    with pytest.raises(
        FileNotFoundError,
        match=expected_error,
    ):
        rd.compare_device({
            "hostname": "TEST1",
            "platform": "Arista EOS",
        })

    parser.assert_not_called()


# ------------------------------------------------------------
# Configuration parsing errors
# ------------------------------------------------------------

def test_compare_device_propagates_parse_failure(
    monkeypatch, isolated_drift_files
):
    """Configuration parsing errors must not be hidden."""

    generated_dir, live_dir = isolated_drift_files

    (generated_dir / "TEST1.cfg").write_text("INTENDED\n")
    (live_dir / "TEST1.cfg").write_text("LIVE\n")

    parser = Mock(
        side_effect=ValueError("Simulated parsing failure")
    )

    monkeypatch.setattr(rd, "parse_config", parser)

    with pytest.raises(
        ValueError,
        match="Simulated parsing failure",
    ):
        rd.compare_device({
            "hostname": "TEST1",
            "platform": "Arista EOS",
        })

    parser.assert_called_once_with(
        "Arista EOS",
        "INTENDED\n",
    )


# ------------------------------------------------------------
# CLI drift reporting workflow
# ------------------------------------------------------------

@pytest.fixture
def cli_devices():
    return [
        {
            "hostname": f"R{i}",
            "platform": "Arista EOS",
        }
        for i in range(1, 5)
    ]


def make_drift_result(device, missing=(), extra=()):
    return {
        "hostname": device["hostname"],
        "platform": device["platform"],
        "missing": list(missing),
        "extra": list(extra),
    }


def test_main_all_devices_pass(
    monkeypatch, capsys, cli_devices
):
    """All devices without drift must pass successfully."""

    monkeypatch.setattr(
        rd, "load_inventory",
        lambda: {"devices": cli_devices},
    )

    comparator = Mock(
        side_effect=lambda device: make_drift_result(device)
    )
    monkeypatch.setattr(rd, "compare_device", comparator)

    monkeypatch.setattr(
        rd.sys, "argv", ["report_drift.py"]
    )

    rd.main()

    captured = capsys.readouterr()

    for device in cli_devices:
        assert f"PASS   {device['hostname']}" in captured.out

    assert captured.out.count("no scoped drift") == 4
    assert captured.err == ""

    assert comparator.call_args_list == [
        call(device) for device in cli_devices
    ]


def test_main_selected_device(
    monkeypatch, capsys, cli_devices
):
    """--device must limit comparison to the selected device."""

    monkeypatch.setattr(
        rd, "load_inventory",
        lambda: {"devices": cli_devices},
    )

    comparator = Mock(
        return_value=make_drift_result(cli_devices[1])
    )
    monkeypatch.setattr(rd, "compare_device", comparator)

    monkeypatch.setattr(
        rd.sys,
        "argv",
        ["report_drift.py", "--device", "R2"],
    )

    rd.main()

    comparator.assert_called_once_with(cli_devices[1])

    output = capsys.readouterr().out

    assert "PASS   R2" in output
    assert "PASS   R1" not in output
    assert "PASS   R3" not in output


def test_main_rejects_unknown_device(
    monkeypatch, capsys, cli_devices
):
    """Unknown managed devices must be rejected."""

    monkeypatch.setattr(
        rd, "load_inventory",
        lambda: {"devices": cli_devices},
    )

    comparator = Mock()
    monkeypatch.setattr(rd, "compare_device", comparator)

    monkeypatch.setattr(
        rd.sys,
        "argv",
        ["report_drift.py", "--device", "R999"],
    )

    with pytest.raises(SystemExit) as exc:
        rd.main()

    assert exc.value.code == 1
    assert "Managed device 'R999' was not found" in (
        capsys.readouterr().err
    )

    comparator.assert_not_called()


def test_main_reports_drift_without_details(
    monkeypatch, capsys, cli_devices
):
    """Drift should fail without exposing detailed paths by default."""

    missing = ("interface Ethernet1", "description expected")
    extra = ("interface Ethernet1", "description changed")

    monkeypatch.setattr(
        rd, "load_inventory",
        lambda: {"devices": [cli_devices[0]]},
    )

    monkeypatch.setattr(
        rd, "compare_device",
        Mock(
            return_value=make_drift_result(
                cli_devices[0],
                missing=[missing],
                extra=[extra],
            )
        ),
    )

    monkeypatch.setattr(
        rd.sys, "argv", ["report_drift.py"]
    )

    with pytest.raises(SystemExit) as exc:
        rd.main()

    assert exc.value.code == 1

    output = capsys.readouterr().out

    assert "DRIFT  R1" in output
    assert "missing=1" in output
    assert "extra=1" in output
    assert "Missing intended paths:" not in output
    assert "Extra live paths:" not in output


def test_main_reports_drift_with_details(
    monkeypatch, capsys, cli_devices
):
    """--details must display the individual drift paths."""

    missing = ("interface Ethernet1", "description expected")
    extra = ("interface Ethernet1", "description changed")

    monkeypatch.setattr(
        rd, "load_inventory",
        lambda: {"devices": [cli_devices[0]]},
    )

    monkeypatch.setattr(
        rd, "compare_device",
        Mock(
            return_value=make_drift_result(
                cli_devices[0],
                missing=[missing],
                extra=[extra],
            )
        ),
    )

    monkeypatch.setattr(
        rd.sys,
        "argv",
        ["report_drift.py", "--details"],
    )

    with pytest.raises(SystemExit) as exc:
        rd.main()

    assert exc.value.code == 1

    output = capsys.readouterr().out

    assert "DRIFT  R1" in output
    assert "Missing intended paths:" in output
    assert "Extra live paths:" in output

    assert rd.format_path(missing) in output
    assert rd.format_path(extra) in output


def test_main_continues_after_device_failure(
    monkeypatch, capsys, cli_devices
):
    """A failed device must not stop remaining drift checks."""

    monkeypatch.setattr(
        rd, "load_inventory",
        lambda: {"devices": cli_devices},
    )

    def fake_compare(device):
        hostname = device["hostname"]

        if hostname == "R2":
            raise ConnectionError("Simulated collection failure")

        if hostname == "R3":
            return make_drift_result(
                device,
                missing=[
                    ("interface Ethernet1", "shutdown")
                ],
            )

        return make_drift_result(device)

    comparator = Mock(side_effect=fake_compare)
    monkeypatch.setattr(rd, "compare_device", comparator)

    monkeypatch.setattr(
        rd.sys, "argv", ["report_drift.py"]
    )

    with pytest.raises(SystemExit) as exc:
        rd.main()

    assert exc.value.code == 1

    assert comparator.call_args_list == [
        call(device) for device in cli_devices
    ]

    captured = capsys.readouterr()

    assert "PASS   R1" in captured.out
    assert "DRIFT  R3" in captured.out
    assert "PASS   R4" in captured.out

    assert "FAIL   R2" in captured.err
    assert "Simulated collection failure" in captured.err


def test_main_empty_inventory(monkeypatch, capsys):
    """An empty inventory must finish without checking devices."""

    monkeypatch.setattr(
        rd, "load_inventory",
        lambda: {"devices": []},
    )

    comparator = Mock()
    monkeypatch.setattr(rd, "compare_device", comparator)

    monkeypatch.setattr(
        rd.sys, "argv", ["report_drift.py"]
    )

    rd.main()

    comparator.assert_not_called()

    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize(
    "missing, extra, expected_counts",
    [
        (
            [("interface Ethernet1", "shutdown")],
            [],
            "missing=1  extra=0",
        ),
        (
            [],
            [("interface Ethernet1", "description extra")],
            "missing=0  extra=1",
        ),
    ],
)
def test_main_single_drift_category(
    monkeypatch,
    capsys,
    cli_devices,
    missing,
    extra,
    expected_counts,
):
    """Missing-only and extra-only drift must be counted correctly."""

    device = cli_devices[0]

    monkeypatch.setattr(
        rd, "load_inventory",
        lambda: {"devices": [device]},
    )

    monkeypatch.setattr(
        rd, "compare_device",
        Mock(
            return_value=make_drift_result(
                device,
                missing=missing,
                extra=extra,
            )
        ),
    )

    monkeypatch.setattr(
        rd.sys, "argv", ["report_drift.py"]
    )

    with pytest.raises(SystemExit) as exc:
        rd.main()

    assert exc.value.code == 1

    output = capsys.readouterr().out

    assert "DRIFT  R1" in output
    assert expected_counts in output
