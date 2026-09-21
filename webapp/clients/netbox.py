import os

import requests

from webapp.config import NETBOX_URL


def netbox_headers():
    token = os.environ.get("NETBOX_TOKEN")

    if not token:
        raise RuntimeError(
            "NETBOX_TOKEN is not available to the web application."
        )

    return {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def netbox_get(path):
    try:
        response = requests.get(
            f"{NETBOX_URL}{path}",
            headers=netbox_headers(),
            timeout=10,
        )

        response.raise_for_status()

        return response.json()

    except requests.RequestException as exc:
        raise RuntimeError(
            f"NetBox GET failed: {exc}"
        ) from exc


def netbox_patch(path, payload):
    try:
        response = requests.patch(
            f"{NETBOX_URL}{path}",
            headers=netbox_headers(),
            json=payload,
            timeout=10,
        )

        response.raise_for_status()

        return response.json()

    except requests.RequestException as exc:
        raise RuntimeError(
            f"NetBox PATCH failed: {exc}"
        ) from exc


def netbox_post(path, payload):
    try:
        response = requests.post(
            f"{NETBOX_URL}{path}",
            headers=netbox_headers(),
            json=payload,
            timeout=10,
        )

        response.raise_for_status()

        return response.json()

    except requests.RequestException as exc:
        detail = ""

        if getattr(exc, "response", None) is not None:
            detail = exc.response.text.strip()

        raise RuntimeError(
            f"NetBox POST failed: {detail or exc}"
        ) from exc


def netbox_delete(path):
    try:
        response = requests.delete(
            f"{NETBOX_URL}{path}",
            headers=netbox_headers(),
            timeout=10,
        )

        if response.status_code != 204:
            response.raise_for_status()

    except requests.RequestException as exc:
        detail = ""

        if getattr(exc, "response", None) is not None:
            detail = exc.response.text.strip()

        raise RuntimeError(
            f"NetBox DELETE failed: {detail or exc}"
        ) from exc
