"""Lab 9 R8/S5 monitoring and Grafana regression tests.

These tests inspect repository configuration only.
No devices, live services, or external APIs are contacted.
"""

from copy import deepcopy
import json
from pathlib import Path
import re
import tomllib

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]

TELEGRAF = (
    ROOT / "configs" / "telegraf" / "telegraf.d"
)

GRAFANA = (
    ROOT / "configs" / "grafana" / "dashboards"
)

NEW_TARGETS = {
    "R8": "172.20.20.20:6030",
    "S5": "172.20.20.21:6030",
}

DEVICE_MAPPINGS = {
    "R2": "172.20.20.5",
    "R8": "172.20.20.20",
    "S5": "172.20.20.21",
}


def read_toml(name):
    with (TELEGRAF / name).open("rb") as stream:
        return tomllib.load(stream)


def read_dashboard(name):
    return json.loads((GRAFANA / name).read_text())


@pytest.mark.parametrize(
    "name",
    [
        "arista.conf",
        "arista_health.conf",
        "arista_ifstate.conf",
    ],
)
def test_arista_collectors_include_r8_and_s5(name):
    """New devices use the existing gNMI collectors."""

    config = read_toml(name)
    collectors = config["inputs"]["gnmi"]

    assert len(collectors) == 1

    addresses = collectors[0]["addresses"]

    assert len(addresses) == 8
    assert len(addresses) == len(set(addresses))

    # Preserve the established R2 collector target.
    assert "172.20.20.5:6030" in addresses

    for address in NEW_TARGETS.values():
        assert addresses.count(address) == 1


@pytest.mark.parametrize(
    "name",
    [
        "interface_normalize.conf",
        "device_health_normalize.conf",
    ],
)
def test_normalizers_identify_new_devices(name):
    """Canonical telemetry must resolve device identity."""

    config = read_toml(name)
    processors = config["processors"]["starlark"]

    assert len(processors) == 1

    source = processors[0]["source"]

    for device, address in DEVICE_MAPPINGS.items():
        expression = (
            rf'(?m)^[ \t]*"{re.escape(address)}"'
            rf'[ \t]*:[ \t]*"{device}"[ \t]*,'
        )

        matches = re.findall(expression, source)

        assert len(matches) == 1, (
            f"{name}: mapping for {device} "
            "missing or duplicated"
        )


def test_r8_ospf_collector_matches_existing_r2_pattern():
    """R8 inherits existing R2 gNMIc OSPF settings."""

    path = ROOT / "configs/gnmic/arista-ospf.yml"

    config = yaml.safe_load(path.read_text())

    targets = config["targets"]

    assert len(targets) == 4
    assert "R8" in targets

    r2_matches = [
        value
        for value in targets.values()
        if value.get("event-tags", {}).get("device") == "R2"
    ]

    assert len(r2_matches) == 1

    r2 = r2_matches[0]
    r8 = targets["R8"]

    assert r2["address"] == "172.20.20.5:6030"

    expected_r8 = deepcopy(r2)
    expected_r8["address"] = "172.20.20.20:6030"
    expected_r8["event-tags"]["device"] = "R8"
    expected_r8["event-tags"]["mgmt_ip"] = "172.20.20.20"

    assert r8 == expected_r8

    # R8 has a unique management target.
    addresses = [
        target["address"]
        for target in targets.values()
    ]

    assert len(addresses) == len(set(addresses))


@pytest.mark.parametrize(
    ("filename", "expected", "panel_count"),
    [
        (
            "interfaces.json",
            [
                "R1", "R2", "R3", "R4", "R8",
                "S1", "S2", "S3", "S4", "S5",
            ],
            13,
        ),
        (
            "routing.json",
            [
                "R1", "R2", "R3", "R4", "R8",
                "S1", "S2", "S3", "S4",
            ],
            9,
        ),
    ],
)
def test_grafana_device_selectors(
    filename,
    expected,
    panel_count,
):
    """Existing Grafana selectors include the right nodes."""

    dashboard = read_dashboard(filename)

    assert len(dashboard["panels"]) == panel_count

    variables = [
        item
        for item in dashboard["templating"]["list"]
        if item.get("name") == "device"
    ]

    assert len(variables) == 1

    selector = variables[0]

    assert selector["type"] == "custom"

    actual = [
        name.strip()
        for name in selector["query"].split(",")
    ]

    assert actual == expected

    option_values = {
        item.get("value")
        for item in selector["options"]
    }

    assert set(expected).issubset(option_values)

    assert selector["includeAll"] is True

    # S5 is an L2 switch, not an OSPF participant.
    if filename == "routing.json":
        assert "S5" not in actual


def test_network_overview_remains_device_dynamic():
    """Existing overview queries exclude R5, not new nodes."""

    dashboard = read_dashboard(
        "network-overview.json"
    )

    assert len(dashboard["panels"]) == 7

    for panel in dashboard["panels"]:
        queries = [
            target.get("query", "")
            for target in panel.get("targets", [])
        ]

        assert queries

        for query in queries:
            assert re.search(
                r'r\.device\s*!=\s*["\']R5["\']',
                query,
            )

            assert not re.search(
                r'r\.device\s*!=\s*["\']'
                r'(?:R8|S5)["\']',
                query,
            )


def test_lab9_documentation_is_linked():
    """Repository README references the Lab 9 record."""

    readme = (ROOT / "README.md").read_text()

    doc_name = (
        "docs/lab9-r8-s5-ztp-monitoring.md"
    )

    assert doc_name in readme

    document = ROOT / doc_name

    assert document.is_file()

    content = document.read_text()

    for requirement in (
        "Device-Initiated ZTP",
        "Streaming Telemetry",
        "Grafana Visualization",
        "DevOps and Jenkins",
        "Acceptance Summary",
    ):
        assert requirement in content

    assert "Graphviz" in content
    assert "Deferred" in content


def test_all_required_grafana_uids_preserved():
    """Do not inadvertently replace existing dashboards."""

    expected = {
        "interfaces.json": "ana-interfaces",
        "routing.json": "ana-routing",
        "network-overview.json": "ana-network-overview",
    }

    for filename, uid in expected.items():
        dashboard = read_dashboard(filename)

        assert dashboard["uid"] == uid
