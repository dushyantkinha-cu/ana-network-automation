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
        lambda hostname=None: {
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


def test_dry_run_deployment_action(monkeypatch):
    monkeypatch.setattr(
        automation_router,
        "find_managed_device",
        lambda hostname: MANAGED_DEVICE,
    )

    called = {}

    def fake_trigger(
        device,
        action,
        confirm_device="",
    ):
        called["device"] = device
        called["action"] = action

        return {
            "status_code": 201,
            "queue_url": (
                "http://jenkins.example/queue/item/10/"
            ),
            "device": device,
            "action": action,
        }

    monkeypatch.setattr(
        automation_router,
        "trigger_deployment",
        fake_trigger,
    )

    response = client.post(
        "/automation/deploy",
        data={
            "device": "R3",
            "action": "dry-run",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        "/automation?device=R3&status=success"
    )

    assert called == {
        "device": "R3",
        "action": "dry-run",
    }


def test_r5_cannot_trigger_dry_run(monkeypatch):
    monkeypatch.setattr(
        automation_router,
        "find_managed_device",
        lambda hostname: {
            "hostname": "R5",
            "automation_managed": False,
        },
    )

    def forbidden_trigger(*args, **kwargs):
        raise AssertionError(
            "Jenkins must not be triggered for R5."
        )

    monkeypatch.setattr(
        automation_router,
        "trigger_deployment",
        forbidden_trigger,
    )

    response = client.post(
        "/automation/deploy",
        data={
            "device": "R5",
            "action": "dry-run",
        },
    )

    assert response.status_code == 403


def test_preview_deployment_action(monkeypatch):
    monkeypatch.setattr(
        automation_router,
        "find_managed_device",
        lambda hostname: MANAGED_DEVICE,
    )

    called = {}

    def fake_trigger(
        device,
        action,
        confirm_device="",
    ):
        called["device"] = device
        called["action"] = action

        return {
            "status_code": 201,
            "queue_url": (
                "http://jenkins.example/queue/item/11/"
            ),
            "device": device,
            "action": action,
        }

    monkeypatch.setattr(
        automation_router,
        "trigger_deployment",
        fake_trigger,
    )

    response = client.post(
        "/automation/deploy",
        data={
            "device": "R3",
            "action": "preview",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        "/automation?device=R3&status=success"
    )

    assert called == {
        "device": "R3",
        "action": "preview",
    }


def test_apply_requires_exact_confirmation(
    monkeypatch,
):
    def forbidden_lookup(*args, **kwargs):
        raise AssertionError(
            "Device lookup must not occur when "
            "Apply confirmation is invalid."
        )

    def forbidden_trigger(*args, **kwargs):
        raise AssertionError(
            "Jenkins must not be triggered when "
            "Apply confirmation is invalid."
        )

    monkeypatch.setattr(
        automation_router,
        "find_managed_device",
        forbidden_lookup,
    )

    monkeypatch.setattr(
        automation_router,
        "trigger_deployment",
        forbidden_trigger,
    )

    response = client.post(
        "/automation/deploy",
        data={
            "device": "R3",
            "action": "apply",
            "confirm_device": "R4",
        },
    )

    assert response.status_code == 400


def test_apply_deployment_action(monkeypatch):
    monkeypatch.setattr(
        automation_router,
        "find_managed_device",
        lambda hostname: MANAGED_DEVICE,
    )

    called = {}

    def fake_trigger(
        device,
        action,
        confirm_device="",
    ):
        called["device"] = device
        called["action"] = action
        called["confirm_device"] = confirm_device

    monkeypatch.setattr(
        automation_router,
        "trigger_deployment",
        fake_trigger,
    )

    response = client.post(
        "/automation/deploy",
        data={
            "device": "R3",
            "action": "apply",
            "confirm_device": "R3",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    assert response.headers["location"].startswith(
        "/automation?device=R3&status=success"
    )

    assert called == {
        "device": "R3",
        "action": "apply",
        "confirm_device": "R3",
    }


def test_r5_cannot_trigger_apply(monkeypatch):
    monkeypatch.setattr(
        automation_router,
        "find_managed_device",
        lambda hostname: {
            "hostname": "R5",
            "automation_managed": False,
        },
    )

    def forbidden_trigger(*args, **kwargs):
        raise AssertionError(
            "Jenkins must never be triggered for R5."
        )

    monkeypatch.setattr(
        automation_router,
        "trigger_deployment",
        forbidden_trigger,
    )

    response = client.post(
        "/automation/deploy",
        data={
            "device": "R5",
            "action": "apply",
            "confirm_device": "R5",
        },
    )

    assert response.status_code == 403


def test_apply_trigger_requires_exact_confirmation():
    from webapp.clients import jenkins

    try:
        jenkins.trigger_deployment(
            device="R3",
            action="apply",
            confirm_device="R4",
        )
    except RuntimeError as exc:
        assert (
            "confirmation must exactly match"
            in str(exc).lower()
        )
    else:
        raise AssertionError(
            "Mismatched apply confirmation "
            "must be rejected."
        )


def test_latest_device_build_filters_by_device(
    monkeypatch,
):
    from webapp.clients import jenkins

    class FakeResponse:
        def json(self):
            return {
                "builds": [
                    {
                        "number": 30,
                        "building": False,
                        "result": "SUCCESS",
                        "actions": [
                            {
                                "parameters": [
                                    {
                                        "name": "DEVICE",
                                        "value": "R1",
                                    },
                                    {
                                        "name": "ACTION",
                                        "value": "apply",
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "number": 29,
                        "building": False,
                        "result": "SUCCESS",
                        "actions": [
                            {
                                "parameters": [
                                    {
                                        "name": "DEVICE",
                                        "value": "R3",
                                    },
                                    {
                                        "name": "ACTION",
                                        "value": "preview",
                                    },
                                ],
                            },
                        ],
                    },
                ],
            }

    monkeypatch.setattr(
        jenkins,
        "jenkins_request",
        lambda *args, **kwargs: FakeResponse(),
    )

    build = jenkins.get_latest_device_build(
        "R3"
    )

    assert build["number"] == 29
    assert build["parameters"]["DEVICE"] == "R3"
    assert (
        build["parameters"]["ACTION"]
        == "preview"
    )


def test_recent_device_builds_filters_and_limits(
    monkeypatch,
):
    from webapp.clients import jenkins

    class FakeResponse:
        def json(self):
            return {
                "builds": [
                    {
                        "number": 30,
                        "building": False,
                        "result": "SUCCESS",
                        "actions": [
                            {
                                "parameters": [
                                    {
                                        "name": "DEVICE",
                                        "value": "R1",
                                    },
                                    {
                                        "name": "ACTION",
                                        "value": "apply",
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "number": 29,
                        "building": False,
                        "result": "SUCCESS",
                        "actions": [
                            {
                                "parameters": [
                                    {
                                        "name": "DEVICE",
                                        "value": "R3",
                                    },
                                    {
                                        "name": "ACTION",
                                        "value": "preview",
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "number": 28,
                        "building": False,
                        "result": "SUCCESS",
                        "actions": [
                            {
                                "parameters": [
                                    {
                                        "name": "DEVICE",
                                        "value": "R1",
                                    },
                                    {
                                        "name": "ACTION",
                                        "value": "preview",
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "number": 27,
                        "building": False,
                        "result": "SUCCESS",
                        "actions": [
                            {
                                "parameters": [
                                    {
                                        "name": "DEVICE",
                                        "value": "R1",
                                    },
                                    {
                                        "name": "ACTION",
                                        "value": "dry-run",
                                    },
                                ],
                            },
                        ],
                    },
                ],
            }

    monkeypatch.setattr(
        jenkins,
        "jenkins_request",
        lambda *args, **kwargs: FakeResponse(),
    )

    builds = jenkins.get_recent_device_builds(
        "R1",
        limit=2,
    )

    assert [
        build["number"]
        for build in builds
    ] == [30, 28]

    assert [
        build["parameters"]["ACTION"]
        for build in builds
    ] == [
        "apply",
        "preview",
    ]


def test_intent_update_runs_automatic_validation(
    monkeypatch,
):
    from urllib.parse import unquote

    events = []

    monkeypatch.setattr(
        changes_router,
        "find_managed_device",
        lambda hostname: {
            "hostname": hostname,
            "automation_managed": True,
            "platform": "Cisco IOS-XE",
            "device_id": 3,
        },
    )

    monkeypatch.setattr(
        changes_router,
        "get_choice_values",
        lambda choice_set_id: [
            {"value": "bgp"},
        ],
    )

    monkeypatch.setattr(
        changes_router,
        "PROFILE_TEMPLATE_MAP",
        {
            "Cisco IOS-XE": {
                "distribution": "template.j2",
            },
        },
    )

    def fake_patch(path, payload):
        events.append(("patch", path))

    def fake_validation(hostname=None):
        events.append(
            ("validate", hostname)
        )

        return {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(
        changes_router,
        "netbox_patch",
        fake_patch,
    )

    monkeypatch.setattr(
        changes_router,
        "run_validation",
        fake_validation,
    )

    response = client.post(
        "/changes/update",
        data={
            "hostname": "R3",
            "config_profile": "distribution",
            "routing_protocols": "bgp",
            "return_to": "/automation",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    location = unquote(
        response.headers["location"]
    )

    assert "status=success" in location
    assert (
        "Automatic validation passed for R3."
        in location
    )

    assert events == [
        (
            "patch",
            "/api/dcim/devices/3/",
        ),
        (
            "validate",
            "R3",
        ),
    ]


def test_validation_failure_keeps_intent_update(
    monkeypatch,
):
    from urllib.parse import unquote

    events = []

    monkeypatch.setattr(
        changes_router,
        "find_managed_device",
        lambda hostname: {
            "hostname": hostname,
            "automation_managed": True,
            "platform": "Cisco IOS-XE",
            "device_id": 3,
        },
    )

    monkeypatch.setattr(
        changes_router,
        "get_choice_values",
        lambda choice_set_id: [
            {"value": "bgp"},
        ],
    )

    monkeypatch.setattr(
        changes_router,
        "PROFILE_TEMPLATE_MAP",
        {
            "Cisco IOS-XE": {
                "distribution": "template.j2",
            },
        },
    )

    def fake_patch(path, payload):
        events.append(("patch", path))

    def fake_validation(hostname=None):
        events.append(
            ("validate", hostname)
        )

        return {
            "returncode": 1,
            "stdout": "drift detected",
            "stderr": "",
        }

    monkeypatch.setattr(
        changes_router,
        "netbox_patch",
        fake_patch,
    )

    monkeypatch.setattr(
        changes_router,
        "run_validation",
        fake_validation,
    )

    response = client.post(
        "/changes/update",
        data={
            "hostname": "R3",
            "config_profile": "distribution",
            "routing_protocols": "bgp",
            "return_to": "/automation",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    location = unquote(
        response.headers["location"]
    )

    assert "status=error" in location
    assert (
        "NetBox intent for R3 "
        "was updated successfully."
        in location
    )
    assert (
        "No deployment was performed."
        in location
    )

    assert events[0][0] == "patch"
    assert events[1] == (
        "validate",
        "R3",
    )


def test_wan_update_runs_automatic_validation(
    monkeypatch,
):
    events = []

    monkeypatch.setattr(
        changes_router,
        "find_managed_device",
        lambda hostname: {
            "hostname": hostname,
            "automation_managed": True,
        },
    )

    monkeypatch.setattr(
        changes_router,
        "get_wan_address_state",
        lambda device: {
            "ipv4": {
                "id": 41,
                "prefixlen": 31,
            },
            "ipv6": {
                "id": 42,
                "prefixlen": 127,
            },
        },
    )

    def fake_patch(path, payload):
        events.append(("patch", path))

    def fake_validation(hostname=None):
        events.append(
            ("validate", hostname)
        )

        return {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(
        changes_router,
        "netbox_patch",
        fake_patch,
    )

    monkeypatch.setattr(
        changes_router,
        "run_validation",
        fake_validation,
    )

    response = client.post(
        "/changes/update-wan",
        data={
            "hostname": "R3",
            "wan_ipv4": "203.0.113.2/31",
            "wan_ipv6": "2001:db8::2/127",
            "return_to": "/automation",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    assert events == [
        (
            "patch",
            "/api/ipam/ip-addresses/41/",
        ),
        (
            "patch",
            "/api/ipam/ip-addresses/42/",
        ),
        (
            "validate",
            "R3",
        ),
    ]


def test_metadata_update_runs_automatic_validation(
    monkeypatch,
):
    events = []

    monkeypatch.setattr(
        changes_router,
        "find_managed_device",
        lambda hostname: {
            "hostname": hostname,
            "automation_managed": True,
            "device_id": 3,
        },
    )

    monkeypatch.setattr(
        changes_router,
        "get_sites",
        lambda: [
            {
                "id": 7,
                "name": "Test Site",
            },
        ],
    )

    def fake_patch(path, payload):
        events.append(("patch", path))

    def fake_validation(hostname=None):
        events.append(
            ("validate", hostname)
        )

        return {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(
        changes_router,
        "netbox_patch",
        fake_patch,
    )

    monkeypatch.setattr(
        changes_router,
        "run_validation",
        fake_validation,
    )

    response = client.post(
        "/changes/update-metadata",
        data={
            "hostname": "R3",
            "site_id": "7",
            "return_to": "/automation",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    assert events == [
        (
            "patch",
            "/api/dcim/devices/3/",
        ),
        (
            "validate",
            "R3",
        ),
    ]
