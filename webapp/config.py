import ipaddress
import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
WEBAPP_DIR = Path(__file__).resolve().parent
AUTOMATION_DIR = REPO_ROOT / "automation"

INVENTORY_SCRIPT = AUTOMATION_DIR / "netbox_inventory.py"
VALIDATION_SCRIPT = AUTOMATION_DIR / "run_validation.py"

VALIDATION_DIR = REPO_ROOT / "validation-reports"
GOLDEN_DIR = REPO_ROOT / "golden-configs"

STATIC_DIR = WEBAPP_DIR / "static"
TEMPLATE_DIR = WEBAPP_DIR / "templates"

NETBOX_URL = os.environ.get(
    "NETBOX_URL",
    "http://netbox.local",
).rstrip("/")

ROUTING_CHOICE_SET_ID = 1
PROFILE_CHOICE_SET_ID = 2

PROFILE_TEMPLATE_MAP = {
    "Cisco IOS-XE": {
        "edge": "templates/cisco/edge.j2",
    },
    "Arista EOS": {
        "distribution": "templates/arista/distribution.j2",
        "access": "templates/arista/access.j2",
        "core": "templates/arista/core.j2",
    },
    "Nokia SR Linux": {
        "core": "templates/nokia/core.j2",
    },
}

NETWORK_VENDORS = {
    "Arista",
    "Cisco",
    "Nokia",
}

MANAGEMENT_NETWORK = ipaddress.ip_network(
    "172.20.20.0/24"
)

PROFILE_ROLE_MAP = {
    "edge": "Router",
    "distribution": "Router",
    "access": "Multilayer Switch",
    "core": "Multilayer Switch",
}

SITE_STATUS_CHOICES = {
    "planned": "Planned",
    "staging": "Staging",
    "active": "Active",
    "decommissioning": "Decommissioning",
    "retired": "Retired",
}

GRAFANA_PORT = 3000

GRAFANA_DASHBOARDS = {
    "overview": {
        "label": "Network Overview",
        "uid": "ana-network-overview",
    },
    "interfaces": {
        "label": "Interfaces",
        "uid": "ana-interfaces",
    },
    "routing": {
        "label": "Routing",
        "uid": "ana-routing",
    },
    "topology": {
        "label": "Live Topology",
        "uid": "adqzdvd",
    },
}
