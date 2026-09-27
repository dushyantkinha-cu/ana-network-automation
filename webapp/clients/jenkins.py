from urllib.parse import quote

import requests

from webapp.config import (
    JENKINS_API_TOKEN,
    JENKINS_DEPLOY_JOB,
    JENKINS_URL,
    JENKINS_USER,
)


DEFAULT_TIMEOUT = 10


def _require_config():
    missing = []

    if not JENKINS_URL:
        missing.append("JENKINS_URL")

    if not JENKINS_USER:
        missing.append("JENKINS_USER")

    if not JENKINS_API_TOKEN:
        missing.append("JENKINS_API_TOKEN")

    if not JENKINS_DEPLOY_JOB:
        missing.append("JENKINS_DEPLOY_JOB")

    if missing:
        raise RuntimeError(
            "Missing Jenkins configuration: "
            + ", ".join(missing)
        )


def _job_path():
    return quote(
        JENKINS_DEPLOY_JOB,
        safe="",
    )


def jenkins_request(
    method,
    path,
    **kwargs,
):
    _require_config()

    url = (
        JENKINS_URL
        + "/"
        + path.lstrip("/")
    )

    try:
        response = requests.request(
            method,
            url,
            auth=(
                JENKINS_USER,
                JENKINS_API_TOKEN,
            ),
            timeout=DEFAULT_TIMEOUT,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Unable to reach Jenkins: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise RuntimeError(
            "Jenkins API request failed with "
            f"HTTP {response.status_code}."
        )

    return response


def get_deployment_job():
    response = jenkins_request(
        "GET",
        (
            f"/job/{_job_path()}/api/json"
            "?tree=name,buildable,color,"
            "lastBuild[number,url],"
            "lastCompletedBuild[number,url,result]"
        ),
    )

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Jenkins returned invalid JSON."
        ) from exc


ALLOWED_DEVICES = {
    "R1",
    "R2",
    "R3",
    "R4",
    "S1",
    "S2",
    "S3",
    "S4",
}

ALLOWED_ACTIONS = {
    "dry-run",
    "preview",
    "apply",
}


def get_crumb():
    response = jenkins_request(
        "GET",
        "/crumbIssuer/api/json",
    )

    try:
        crumb = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Jenkins returned invalid crumb JSON."
        ) from exc

    field = crumb.get("crumbRequestField")
    value = crumb.get("crumb")

    if not field or not value:
        raise RuntimeError(
            "Jenkins returned an incomplete CSRF crumb."
        )

    return {
        "field": field,
        "value": value,
    }


def trigger_deployment(
    device,
    action,
    confirm_device="",
):
    if device not in ALLOWED_DEVICES:
        raise RuntimeError(
            f"Device is not allowed for deployment: {device}"
        )

    if action not in ALLOWED_ACTIONS:
        raise RuntimeError(
            f"Unsupported deployment action: {action}"
        )

    if (
        action == "apply"
        and confirm_device != device
    ):
        raise RuntimeError(
            "Apply confirmation must exactly match "
            "the target device."
        )

    crumb = get_crumb()

    response = jenkins_request(
        "POST",
        (
            f"/job/{_job_path()}/"
            "buildWithParameters"
        ),
        headers={
            crumb["field"]: crumb["value"],
        },
        data={
            "DEVICE": device,
            "ACTION": action,
            "CONFIRM_DEVICE": confirm_device,
        },
        allow_redirects=False,
    )

    queue_url = response.headers.get("Location")

    if not queue_url:
        raise RuntimeError(
            "Jenkins accepted the request but did not "
            "return a queue location."
        )

    return {
        "status_code": response.status_code,
        "queue_url": queue_url,
        "device": device,
        "action": action,
    }


def resolve_queue_build(
    queue_url,
    attempts=20,
    delay=0.5,
):
    import time
    from urllib.parse import urlparse

    queue_path = urlparse(
        queue_url
    ).path.rstrip("/")

    for _ in range(attempts):
        response = jenkins_request(
            "GET",
            queue_path + "/api/json",
        )

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "Jenkins returned invalid queue JSON."
            ) from exc

        executable = data.get("executable") or {}
        build_number = executable.get("number")

        if build_number is not None:
            return build_number

        if data.get("cancelled"):
            raise RuntimeError(
                "Jenkins queue item was cancelled."
            )

        time.sleep(delay)

    raise RuntimeError(
        "Jenkins build did not leave the queue "
        "in time."
    )


def get_deployment_build(build_number):
    response = jenkins_request(
        "GET",
        (
            f"/job/{_job_path()}/{int(build_number)}"
            "/api/json"
            "?tree=number,url,building,result,"
            "actions[parameters[name,value]]"
        ),
    )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Jenkins returned invalid build JSON."
        ) from exc

    parameters = {}

    for action in data.get("actions", []):
        for parameter in action.get(
            "parameters",
            [],
        ):
            name = parameter.get("name")

            if name:
                parameters[name] = parameter.get(
                    "value"
                )

    data["parameters"] = parameters

    return data

def get_recent_device_builds(
    device,
    limit=5,
):
    if device not in ALLOWED_DEVICES:
        raise RuntimeError(
            f"Device is not allowed for deployment: {device}"
        )

    if limit < 1:
        raise RuntimeError(
            "Deployment history limit must be at least 1."
        )

    response = jenkins_request(
        "GET",
        (
            f"/job/{_job_path()}/api/json"
            "?tree=builds["
            "number,url,building,result,timestamp,duration,"
            "actions[parameters[name,value]]"
            "]"
        ),
    )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Jenkins returned invalid build-list JSON."
        ) from exc

    matching_builds = []

    for build in data.get("builds", []):
        parameters = {}

        for action in build.get("actions", []):
            for parameter in action.get(
                "parameters",
                [],
            ):
                name = parameter.get("name")

                if name:
                    parameters[name] = parameter.get(
                        "value"
                    )

        build["parameters"] = parameters

        if parameters.get("DEVICE") == device:
            matching_builds.append(build)

    matching_builds.sort(
        key=lambda build: build.get("number", 0),
        reverse=True,
    )

    return matching_builds[:limit]


def get_latest_device_build(device):
    builds = get_recent_device_builds(
        device,
        limit=1,
    )

    if builds:
        return builds[0]

    return None
