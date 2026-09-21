from fastapi.testclient import TestClient

import webapp.main as main
import webapp.routers.core as core_router
import webapp.routers.automation as automation_router
import webapp.routers.changes as changes_router
import webapp.routers.sites as sites_router
import webapp.routers.onboarding as onboarding_router


client = TestClient(main.app)


MANAGED_DEVICE = {
    "hostname": "R3",
    "device_id": 7,
    "platform": "Cisco IOS-XE",
    "manufacturer": "Cisco",
    "role": "Router",
    "site": "ANA-Lab",
    "management_ip": "172.20.20.9/24",
    "automation_managed": True,
    "routing_protocols": [
        "bgp",
        "ospfv2",
        "ospfv3",
    ],
    "config_profile": "edge",
}


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "application": "ANA Network Automation",
    }


def test_api_inventory(monkeypatch):
    inventory = {
        "devices": [MANAGED_DEVICE],
    }

    monkeypatch.setattr(
        core_router,
        "load_inventory",
        lambda: inventory,
    )

    response = client.get("/api/inventory")

    assert response.status_code == 200
    assert response.json() == inventory


def test_dashboard(monkeypatch):
    monkeypatch.setattr(
        core_router,
        "get_devices",
        lambda: [MANAGED_DEVICE],
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "ANA Network Automation" in response.text


def test_inventory_page(monkeypatch):
    monkeypatch.setattr(
        core_router,
        "get_devices",
        lambda: [MANAGED_DEVICE],
    )

    response = client.get("/inventory")

    assert response.status_code == 200
    assert "R3" in response.text


def test_automation_page(monkeypatch):
    monkeypatch.setattr(
        automation_router,
        "get_devices",
        lambda: [MANAGED_DEVICE],
    )

    monkeypatch.setattr(
        automation_router,
        "latest_validation_report",
        lambda: None,
    )

    monkeypatch.setattr(
        automation_router,
        "golden_snapshots",
        lambda: [],
    )

    response = client.get("/automation")

    assert response.status_code == 200


def test_validation_action_success(monkeypatch):
    monkeypatch.setattr(
        automation_router,
        "run_validation",
        lambda: {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        },
    )

    response = client.post(
        "/automation/validate",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        "/automation?status=success"
    )


def test_changes_page(monkeypatch):
    monkeypatch.setattr(
        changes_router,
        "get_devices",
        lambda: [MANAGED_DEVICE],
    )

    monkeypatch.setattr(
        changes_router,
        "get_choice_values",
        lambda choice_set_id: [],
    )

    monkeypatch.setattr(
        changes_router,
        "get_sites",
        lambda: [
            {
                "id": 1,
                "name": "ANA-Lab",
            }
        ],
    )

    response = client.get("/changes")

    assert response.status_code == 200


def test_new_site_page():
    response = client.get("/changes/new-site")

    assert response.status_code == 200
    assert "Add Site" in response.text


def test_new_device_page(monkeypatch):
    monkeypatch.setattr(
        onboarding_router,
        "get_sites",
        lambda: [],
    )

    monkeypatch.setattr(
        onboarding_router,
        "get_device_types",
        lambda: [],
    )

    monkeypatch.setattr(
        onboarding_router,
        "get_platforms",
        lambda: [],
    )

    monkeypatch.setattr(
        onboarding_router,
        "get_network_roles",
        lambda: [],
    )

    monkeypatch.setattr(
        onboarding_router,
        "get_choice_values",
        lambda choice_set_id: [],
    )

    monkeypatch.setattr(
        onboarding_router,
        "get_staged_devices",
        lambda: [],
    )

    response = client.get("/changes/new")

    assert response.status_code == 200
    assert "Add Device" in response.text


def test_monitoring_dashboards():
    expected = {
        "overview": "ana-network-overview",
        "interfaces": "ana-interfaces",
        "routing": "ana-routing",
        "topology": "adqzdvd",
    }

    for view, uid in expected.items():
        response = client.get(
            f"/monitoring?view={view}"
        )

        assert response.status_code == 200
        assert uid in response.text


def test_unknown_monitoring_dashboard():
    response = client.get(
        "/monitoring?view=does-not-exist"
    )

    assert response.status_code == 404


def test_r5_cannot_update_intent(monkeypatch):
    monkeypatch.setattr(
        changes_router,
        "find_managed_device",
        lambda hostname: {
            "hostname": "R5",
            "automation_managed": False,
        },
    )

    response = client.post(
        "/changes/update",
        data={
            "hostname": "R5",
            "config_profile": "edge",
            "routing_protocols": "bgp",
        },
    )

    assert response.status_code == 403


def test_r5_cannot_update_wan(monkeypatch):
    monkeypatch.setattr(
        changes_router,
        "find_managed_device",
        lambda hostname: {
            "hostname": "R5",
            "automation_managed": False,
        },
    )

    response = client.post(
        "/changes/update-wan",
        data={
            "hostname": "R5",
            "wan_ipv4": "203.0.114.0/31",
            "wan_ipv6": "2001:db8:1::/127",
        },
    )

    assert response.status_code == 403


def test_r5_cannot_update_metadata(monkeypatch):
    monkeypatch.setattr(
        changes_router,
        "find_managed_device",
        lambda hostname: {
            "hostname": "R5",
            "automation_managed": False,
        },
    )

    response = client.post(
        "/changes/update-metadata",
        data={
            "hostname": "R5",
            "site_id": "1",
        },
    )

    assert response.status_code == 403


def test_invalid_site_status_creates_nothing(
    monkeypatch,
):
    def forbidden_post(*args, **kwargs):
        raise AssertionError(
            "netbox_post must not be called"
        )

    monkeypatch.setattr(
        sites_router,
        "netbox_post",
        forbidden_post,
    )

    response = client.post(
        "/changes/new-site",
        data={
            "name": "TEST BAD STATUS",
            "status": "invalid-status",
            "description": "test",
        },
    )

    assert response.status_code == 400


def test_invalid_hostname_creates_nothing(
    monkeypatch,
):
    def forbidden_post(*args, **kwargs):
        raise AssertionError(
            "netbox_post must not be called"
        )

    monkeypatch.setattr(
        onboarding_router,
        "netbox_post",
        forbidden_post,
    )

    response = client.post(
        "/changes/new",
        data={
            "hostname": "!bad hostname!",
            "site_id": "1",
            "vendor_id": "1",
            "device_type_id": "1",
            "platform_id": "1",
            "role_id": "1",
            "management_ip": "172.20.20.250/24",
            "config_profile": "edge",
        },
    )

    assert response.status_code == 400
