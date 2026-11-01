"""Lab 9 regression tests for Arista interface-level OSPF."""

from copy import deepcopy

from automation.render_config import build_environment


def make_context():
    """Minimal synthetic context for the distribution template."""
    return {
        "router_id": "10.255.255.12",
        "ospf": {
            "process_id": 1,
            "passive_interfaces": [],
            "redistribute_isis": {
                "level": "level-1",
                "route_map": "RM-ISIS-TO-OSPF",
            },
            "networks": [
                {"prefix": "10.1.0.0/16", "area": "0.0.0.0"}
            ],
            "max_lsa": 12000,
        },
        "ospfv3": {
            "process_id": 1,
            "interfaces": [],
            "redistribute_isis": {
                "level": "level-1",
                "route_map": "RM-ISIS-TO-OSPF3",
            },
        },
        "isis": {
            "process_id": 1,
            "interfaces": [],
            "net": "49.0001.0000.0000.0002.00",
            "is_type": "level-1",
            "redistribute_ospf_ipv4": {
                "include_leaked": False,
                "match": "internal",
            },
            "redistribute_ospfv3_ipv6": {
                "match": "internal",
                "route_map": "RM-OSPF3-TO-ISIS",
            },
            "ipv6_multi_topology": False,
        },
        "prefix_lists": {
            "ipv4": {},
            "ipv6": {},
        },
        "route_maps": {
            "isis_to_ospf": {
                "name": "RM-ISIS-TO-OSPF",
                "match_ipv4_prefix_list": "PL-TEST",
            },
            "isis_to_ospfv3": {
                "name": "RM-ISIS-TO-OSPF3",
                "match_ipv6_prefix_list": "PL-TEST6",
            },
            "ospfv3_to_isis": {
                "name": "RM-OSPF3-TO-ISIS",
                "match_ipv6_prefix_list": "PL-TEST6",
            },
        },
    }


def render(areas=None, point_to_point=None, ports=None):
    ctx = make_context()

    if areas is not None:
        ctx["ospf"]["interface_areas"] = areas

    if point_to_point is not None:
        ctx["ospf"]["point_to_point_interfaces"] = point_to_point

    if ports is None:
        ports = ["Et2"]

    interfaces = [
        {
            "name": port,
            "description": "",
            "mgmt_only": False,
            "enabled": True,
            "ip_addresses": ["10.1.0.14/31"],
        }
        for port in ports
    ]

    device = {
        "hostname": "TEST-R2",
        "interfaces": interfaces,
    }

    env = build_environment()
    template = env.get_template("arista/distribution.j2")

    return template.render(device=device, ctx=deepcopy(ctx))


def interface_section(config, name):
    marker = f"interface {name}\n"
    assert marker in config
    return config.split(marker, 1)[1].split("\n!", 1)[0]


def test_area20_interface_configuration():
    config = render(
        areas={"Et2": "0.0.0.20"},
        point_to_point=["Et2"],
    )

    section = interface_section(config, "Ethernet2")

    assert "ip address 10.1.0.14/31" in section
    assert "ip ospf area 0.0.0.20" in section
    assert "ip ospf network point-to-point" in section


def test_existing_area0_configuration_preserved():
    config = render(
        areas={"Et2": "0.0.0.20"},
        point_to_point=["Et2"],
    )

    assert "network 10.1.0.0/16 area 0.0.0.0" in config


def test_optional_ospf_settings_do_not_change_old_devices():
    config = render()

    section = interface_section(config, "Ethernet2")

    assert "ip ospf area" not in section
    assert "ip ospf network point-to-point" not in section


def test_ospf_area_settings_are_interface_specific():
    config = render(
        areas={"Et2": "0.0.0.20"},
        point_to_point=["Et2"],
        ports=["Et1", "Et2"],
    )

    et1 = interface_section(config, "Ethernet1")
    et2 = interface_section(config, "Ethernet2")

    assert "ip ospf area 0.0.0.20" not in et1
    assert "ip ospf network point-to-point" not in et1

    assert "ip ospf area 0.0.0.20" in et2
    assert "ip ospf network point-to-point" in et2
