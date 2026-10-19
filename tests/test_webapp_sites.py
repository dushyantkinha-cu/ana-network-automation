"""Isolated unit tests for the FastAPI Sites Router."""

import asyncio
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from starlette.datastructures import FormData

import webapp.routers.sites as sites


# ------------------------------------------------------------
# Mocked environment: never contact real NetBox
# ------------------------------------------------------------

@pytest.fixture
def isolated_sites(monkeypatch):
    statuses = ("active", "planned")

    monkeypatch.setattr(
        sites, "SITE_STATUS_CHOICES", statuses
    )
    monkeypatch.setattr(
        sites, "NETBOX_URL", "https://netbox.example"
    )

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "Unexpected external NetBox operation"
        )

    reader = Mock(side_effect=forbidden)
    writer = Mock(side_effect=forbidden)

    monkeypatch.setattr(sites, "netbox_get", reader)
    monkeypatch.setattr(sites, "netbox_post", writer)

    return {
        "reader": reader,
        "writer": writer,
        "statuses": statuses,
    }


def make_site_request(**overrides):
    fields = {
        "name": "Test Site",
        "status": "active",
        "description": " Site description ",
    }
    fields.update(overrides)

    request = Mock()
    request.form = AsyncMock(
        return_value=FormData(list(fields.items()))
    )
    return request


def submit_site(**overrides):
    return asyncio.run(
        sites.create_site(
            make_site_request(**overrides)
        )
    )


# ------------------------------------------------------------
# GET /changes/new-site
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "status, message",
    [
        (None, None),
        ("success", "Site created successfully"),
    ],
)
def test_new_site_page_context(
    monkeypatch,
    isolated_sites,
    status,
    message,
):
    renderer = Mock()

    monkeypatch.setattr(
        sites.templates,
        "TemplateResponse",
        renderer,
    )

    request = Mock()

    result = sites.new_site_page(
        request=request,
        status=status,
        message=message,
    )

    assert result is renderer.return_value

    renderer.assert_called_once_with(
        request=request,
        name="new_site.html",
        context={
            "page_title": "Add Site",
            "site_status_choices": (
                isolated_sites["statuses"]
            ),
            "action_status": status,
            "action_message": message,
            "netbox_url": "https://netbox.example",
        },
    )

    isolated_sites["reader"].assert_not_called()
    isolated_sites["writer"].assert_not_called()


# ------------------------------------------------------------
# Required site name
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["", "    "],
)
def test_create_site_requires_name(
    isolated_sites,
    name,
):
    with pytest.raises(HTTPException) as exc:
        submit_site(name=name)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Site name is required."

    isolated_sites["reader"].assert_not_called()
    isolated_sites["writer"].assert_not_called()


# ------------------------------------------------------------
# Site name length
# ------------------------------------------------------------

def test_create_site_rejects_long_name(
    isolated_sites,
):
    with pytest.raises(HTTPException) as exc:
        submit_site(name="S" * 101)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Site name is too long."

    isolated_sites["reader"].assert_not_called()
    isolated_sites["writer"].assert_not_called()


# ------------------------------------------------------------
# Site status choices
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "status",
    ["", "unknown-status"],
)
def test_create_site_rejects_invalid_status(
    isolated_sites,
    status,
):
    with pytest.raises(HTTPException) as exc:
        submit_site(status=status)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid site status."

    isolated_sites["reader"].assert_not_called()
    isolated_sites["writer"].assert_not_called()


# ------------------------------------------------------------
# Slug validation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["!!!", "---", "###"],
)
def test_create_site_rejects_empty_slug(
    isolated_sites,
    name,
):
    with pytest.raises(HTTPException) as exc:
        submit_site(name=name)

    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "Site name could not be converted "
        "to a valid NetBox slug."
    )

    isolated_sites["reader"].assert_not_called()
    isolated_sites["writer"].assert_not_called()


# ------------------------------------------------------------
# Duplicate site name
# ------------------------------------------------------------

def test_create_site_rejects_duplicate_name(
    isolated_sites,
):
    reader = isolated_sites["reader"]

    reader.side_effect = None
    reader.return_value = {
        "results": [
            {"id": 10, "name": "Test Site"},
        ],
    }

    with pytest.raises(HTTPException) as exc:
        submit_site()

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "A NetBox site with this name already exists."
    )

    reader.assert_called_once_with(
        "/api/dcim/sites/?name=Test%20Site"
    )

    isolated_sites["writer"].assert_not_called()


# ------------------------------------------------------------
# Duplicate site slug
# ------------------------------------------------------------

def test_create_site_rejects_duplicate_slug(
    isolated_sites,
):
    reader = isolated_sites["reader"]

    reader.side_effect = [
        {"results": []},
        {
            "results": [
                {"id": 11, "slug": "test-site"},
            ],
        },
    ]

    with pytest.raises(HTTPException) as exc:
        submit_site()

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "A NetBox site with this slug already exists."
    )

    assert [
        entry.args[0]
        for entry in reader.call_args_list
    ] == [
        "/api/dcim/sites/?name=Test%20Site",
        "/api/dcim/sites/?slug=test-site",
    ]

    isolated_sites["writer"].assert_not_called()


# ------------------------------------------------------------
# Successful site creation
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "raw_name, status, raw_description, expected_name, slug, description",
    [
        (
            "  North & South Site  ",
            "active",
            "  Lab site  ",
            "North & South Site",
            "north-south-site",
            "Lab site",
        ),
        (
            "Lab-42",
            "planned",
            "",
            "Lab-42",
            "lab-42",
            "",
        ),
    ],
)
def test_create_site_success(
    isolated_sites,
    raw_name,
    status,
    raw_description,
    expected_name,
    slug,
    description,
):
    reader = isolated_sites["reader"]
    writer = isolated_sites["writer"]

    reader.side_effect = [
        {"results": []},
        {"results": []},
    ]

    writer.side_effect = None
    writer.return_value = {
        "id": 101,
        "name": expected_name,
        "slug": slug,
    }

    result = submit_site(
        name=raw_name,
        status=status,
        description=raw_description,
    )

    assert result.status_code == 303

    redirect = urlsplit(result.headers["location"])
    params = parse_qs(redirect.query)

    assert redirect.path == "/changes/new-site"
    assert params["status"] == ["success"]

    assert params["message"] == [
        f"Site '{expected_name}' was created "
        f"in NetBox with slug '{slug}'."
    ]

    from urllib.parse import quote

    assert [
        entry.args[0]
        for entry in reader.call_args_list
    ] == [
        f"/api/dcim/sites/?name={quote(expected_name)}",
        f"/api/dcim/sites/?slug={quote(slug)}",
    ]

    writer.assert_called_once_with(
        "/api/dcim/sites/",
        {
            "name": expected_name,
            "slug": slug,
            "status": status,
            "description": description,
        },
    )


# ------------------------------------------------------------
# NetBox lookup and creation failures
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "failure_stage, expected_reads, expected_writes",
    [
        ("name_lookup", 1, 0),
        ("slug_lookup", 2, 0),
        ("site_creation", 2, 1),
    ],
)
def test_create_site_handles_netbox_failures(
    isolated_sites,
    failure_stage,
    expected_reads,
    expected_writes,
):
    reader = isolated_sites["reader"]
    writer = isolated_sites["writer"]

    original_error = RuntimeError(
        f"Simulated NetBox {failure_stage} failure"
    )

    if failure_stage == "name_lookup":
        reader.side_effect = original_error

    elif failure_stage == "slug_lookup":
        reader.side_effect = [
            {"results": []},
            original_error,
        ]

    else:
        reader.side_effect = [
            {"results": []},
            {"results": []},
        ]
        writer.side_effect = original_error

    with pytest.raises(HTTPException) as exc:
        submit_site()

    assert exc.value.status_code == 503
    assert exc.value.detail == str(original_error)
    assert exc.value.__cause__ is original_error

    assert reader.call_count == expected_reads
    assert writer.call_count == expected_writes
