"""Unit tests for automation.render_config."""

import pytest
from jinja2 import UndefinedError

from automation import render_config as rc


# ------------------------------------------------------------
# Vendor-specific interface naming
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "name, expected",
    [
        ("Loopback", "Loopback0"),
        ("Gi2", "GigabitEthernet2"),
        ("Gi12", "GigabitEthernet12"),
        ("GiX", "GiX"),
    ],
)
def test_ios_ifname(name, expected):
    assert rc.ios_ifname(name) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Loopback", "Loopback0"),
        ("Et1", "Ethernet1"),
        ("Et10", "Ethernet10"),
        ("EtX", "EtX"),
    ],
)
def test_arista_ifname(name, expected):
    assert rc.arista_ifname(name) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Loopback", "lo0"),
        ("Et-1/1", "ethernet-1/1"),
        ("ethernet-1/2", "ethernet-1/2"),
    ],
)
def test_srl_ifname(name, expected):
    assert rc.srl_ifname(name) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Et-1/1", "ethernet-1/1.0"),
        ("Loopback", "lo0.0"),
    ],
)
def test_srl_subifname(name, expected):
    assert rc.srl_subifname(name) == expected


# ------------------------------------------------------------
# IP address validation and conversion
# ------------------------------------------------------------

def test_addresses_of_family():
    addresses = [
        "10.0.0.1/31",
        "2001:db8::1/127",
        "10.0.0.2/24",
    ]

    assert rc.addresses_of_family(addresses, 4) == [
        "10.0.0.1/31",
        "10.0.0.2/24",
    ]

    assert rc.addresses_of_family(addresses, 6) == [
        "2001:db8::1/127",
    ]


@pytest.mark.parametrize(
    "address, expected",
    [
        ("10.0.0.1/31", 4),
        ("2001:db8::1/127", 6),
    ],
)
def test_ip_family(address, expected):
    assert rc.ip_family(address) == expected


@pytest.mark.parametrize(
    "address, expected",
    [
        ("10.0.0.1/31", "10.0.0.1"),
        ("2001:db8::1/127", "2001:db8::1"),
    ],
)
def test_ip_address_only(address, expected):
    assert rc.ip_address_only(address) == expected


@pytest.mark.parametrize(
    "address, expected",
    [
        ("10.0.0.1/24", "255.255.255.0"),
        ("10.0.0.1/31", "255.255.255.254"),
    ],
)
def test_ios_netmask(address, expected):
    assert rc.ios_netmask(address) == expected


def test_ios_netmask_rejects_ipv6():
    with pytest.raises(ValueError, match="not an IPv4 address"):
        rc.ios_netmask("2001:db8::1/64")


# ------------------------------------------------------------
# Vendor template selection
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "platform, profile, expected",
    [
        ("Cisco IOS-XE", "edge", "cisco/edge.j2"),
        ("Arista EOS", "distribution", "arista/distribution.j2"),
        ("Arista EOS", "access", "arista/access.j2"),
        ("Arista EOS", "core", "arista/core.j2"),
        ("Nokia SR Linux", "core", "nokia/core.j2"),
    ],
)
def test_template_mapping(platform, profile, expected):
    assert rc.TEMPLATE_MAP[(platform, profile)] == expected


# ------------------------------------------------------------
# Jinja2 environment
# ------------------------------------------------------------

def test_build_environment():
    env = rc.build_environment()

    assert "ios_ifname" in env.filters
    assert "arista_ifname" in env.filters
    assert "srl_ifname" in env.filters
    assert "ios_netmask" in env.filters
    assert "addresses_of_family" in env.filters

    template = env.from_string(
        "{{ interface | arista_ifname }}"
    )

    assert template.render(interface="Et1") == "Ethernet1"

    # StrictUndefined must reject missing template variables.
    with pytest.raises(UndefinedError):
        env.from_string("{{ missing_variable }}").render()


# ------------------------------------------------------------
# Device lookup
# ------------------------------------------------------------

def test_find_device_success():
    inventory = {
        "devices": [
            {"hostname": "R1"},
            {"hostname": "R2"},
        ]
    }

    result = rc.find_device(inventory, "R2")

    assert result["hostname"] == "R2"


def test_find_device_unknown(capsys):
    inventory = {
        "devices": [
            {"hostname": "R1"},
        ]
    }

    with pytest.raises(SystemExit) as exc:
        rc.find_device(inventory, "R9")

    assert exc.value.code == 1
    assert "Managed device 'R9' was not found" in (
        capsys.readouterr().err
    )


# ------------------------------------------------------------
# Inventory loading using mocked subprocess responses
# ------------------------------------------------------------

def test_load_inventory_success(monkeypatch):
    import json
    from types import SimpleNamespace

    expected = {
        "managed_device_count": 1,
        "devices": [
            {
                "hostname": "R1",
                "platform": "Arista EOS",
                "config_profile": "distribution",
            }
        ],
    }

    def fake_run(command, **kwargs):
        assert command == [
            rc.sys.executable,
            str(rc.INVENTORY_SCRIPT),
        ]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True

        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(expected),
            stderr="",
        )

    monkeypatch.setattr(rc.subprocess, "run", fake_run)

    assert rc.load_inventory() == expected


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
def test_load_inventory_subprocess_failure(
    monkeypatch, capsys, stderr, expected_error
):
    from types import SimpleNamespace

    monkeypatch.setattr(
        rc.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=stderr,
        ),
    )

    with pytest.raises(SystemExit) as exc:
        rc.load_inventory()

    assert exc.value.code == 1
    assert expected_error in capsys.readouterr().err


def test_load_inventory_invalid_json(monkeypatch, capsys):
    from types import SimpleNamespace

    monkeypatch.setattr(
        rc.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="invalid json",
            stderr="",
        ),
    )

    with pytest.raises(SystemExit) as exc:
        rc.load_inventory()

    assert exc.value.code == 1
    assert "Invalid inventory JSON" in capsys.readouterr().err


# ------------------------------------------------------------
# Main configuration rendering workflow
# ------------------------------------------------------------

@pytest.fixture
def isolated_renderer(tmp_path, monkeypatch):
    """Redirect all template and output paths to temporary storage."""

    template_root = tmp_path / "templates"
    template_root.mkdir()

    monkeypatch.setattr(rc, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rc, "TEMPLATE_ROOT", template_root)

    return tmp_path


def prepare_render_test(
    monkeypatch, root, platform, profile, template_text
):
    """Prepare simulated inventory and a temporary Jinja2 template."""

    device = {
        "hostname": "TEST1",
        "platform": platform,
        "config_profile": profile,
        "config_context": {
            "marker": "Unit-Test",
        },
    }

    monkeypatch.setattr(
        rc,
        "load_inventory",
        lambda: {"devices": [device]},
    )

    template_name = rc.TEMPLATE_MAP[(platform, profile)]
    template_file = root / "templates" / template_name

    template_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    template_file.write_text(template_text)

    return device


@pytest.mark.parametrize(
    "platform, profile",
    [
        ("Cisco IOS-XE", "edge"),
        ("Arista EOS", "distribution"),
        ("Arista EOS", "access"),
        ("Arista EOS", "core"),
        ("Nokia SR Linux", "core"),
    ],
)
def test_main_render_stdout_all_platforms(
    monkeypatch,
    capsys,
    isolated_renderer,
    platform,
    profile,
):
    """Every supported platform/profile must render successfully."""

    prepare_render_test(
        monkeypatch,
        isolated_renderer,
        platform,
        profile,
        "hostname={{ device.hostname }}\n"
        "marker={{ ctx.marker }}\n",
    )

    monkeypatch.setattr(
        rc.sys,
        "argv",
        ["render_config.py", "--device", "TEST1", "--stdout"],
    )

    rc.main()

    output = capsys.readouterr().out

    assert output == (
        "hostname=TEST1\n"
        "marker=Unit-Test\n"
    )

    # --stdout must not create output files.
    assert not (
        isolated_renderer / "generated-configs"
    ).exists()


def test_main_writes_generated_config(
    monkeypatch, capsys, isolated_renderer
):
    """Rendered configurations must be written to the output directory."""

    prepare_render_test(
        monkeypatch,
        isolated_renderer,
        "Arista EOS",
        "distribution",
        "interface {{ 'Et1' | arista_ifname }}\n"
        "   description {{ ctx.marker }}\n",
    )

    monkeypatch.setattr(
        rc.sys,
        "argv",
        ["render_config.py", "--device", "TEST1"],
    )

    rc.main()

    output_file = (
        isolated_renderer
        / "generated-configs"
        / "TEST1.cfg"
    )

    assert output_file.is_file()

    assert output_file.read_text() == (
        "interface Ethernet1\n"
        "   description Unit-Test\n"
    )

    assert "Rendered TEST1 ->" in (
        capsys.readouterr().out
    )


def test_main_rejects_unsupported_profile(
    monkeypatch, capsys, isolated_renderer
):
    """Unsupported platform/profile combinations must fail."""

    device = {
        "hostname": "TEST1",
        "platform": "Cisco IOS-XE",
        "config_profile": "core",
        "config_context": {},
    }

    monkeypatch.setattr(
        rc,
        "load_inventory",
        lambda: {"devices": [device]},
    )

    monkeypatch.setattr(
        rc.sys,
        "argv",
        ["render_config.py", "--device", "TEST1"],
    )

    with pytest.raises(SystemExit) as exc:
        rc.main()

    assert exc.value.code == 1

    assert "No template is defined" in (
        capsys.readouterr().err
    )

    assert not (
        isolated_renderer / "generated-configs"
    ).exists()


def test_main_rejects_missing_template_variable(
    monkeypatch, isolated_renderer
):
    """StrictUndefined must reject incomplete template data."""

    prepare_render_test(
        monkeypatch,
        isolated_renderer,
        "Arista EOS",
        "distribution",
        "{{ ctx.required_value }}\n",
    )

    monkeypatch.setattr(
        rc.sys,
        "argv",
        ["render_config.py", "--device", "TEST1", "--stdout"],
    )

    with pytest.raises(UndefinedError):
        rc.main()

    assert not (
        isolated_renderer / "generated-configs"
    ).exists()
