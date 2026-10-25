"""Member import/export (roadmap B-110).

The golden file is the nonprofit's real spreadsheet at its
messiest: a duplicate row, a blank line, a quoted comma name,
a bad email, a stray trailing column. The import must survive
all of it, report exactly what it did row by row, and a dry
run must change nothing at all.
"""

import csv
import io
from pathlib import Path

import pytest

from conftest import bearer, make_group, register, unique_email
from test_groups import invite_and_accept

GOLDEN = (
    Path(__file__).parent / "golden" / "members_messy.csv"
).read_bytes()


@pytest.fixture()
def world(client):
    owner = register(client)
    group = make_group(client, owner)
    member = invite_and_accept(
        client, owner, group["id"], unique_email(), "member"
    )
    return {"owner": owner, "member": member, "group": group}


def _import(client, world, dry_run=False, who=None):
    return client.post(
        f"/api/v1/groups/{world['group']['id']}/members/import"
        + ("?dry_run=true" if dry_run else ""),
        content=GOLDEN,
        headers={
            **bearer(who or world["owner"]),
            "Content-Type": "text/plain",
        },
    )


def _check_messy_report(report):
    """The golden expectations, shared by the dry and real runs."""
    assert report["rows_total"] == 7
    assert report["blank_rows"] == 1
    assert report["created_users"] == 5
    assert report["memberships_created"] == 5
    assert report["enrolments"] == 3
    assert report["already_members"] == 0
    assert report["subgroups_created"] == ["Kitchen Crew"]
    # Row 5 repeats row 2 exactly: skipped, and said so.
    assert [s["row"] for s in report["skipped"]] == [5]
    assert "duplicate" in report["skipped"][0]["reason"]
    # Row 7's email cannot be read: an error row, nobody created.
    assert [e["row"] for e in report["errors"]] == [7]
    assert "not-an-email" in report["errors"][0]["reason"]


def test_dry_run_reports_everything_and_changes_nothing(client, world):
    gid = world["group"]["id"]
    response = _import(client, world, dry_run=True)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["dry_run"] is True
    _check_messy_report(report)

    # Nothing landed: the roster is still exactly who the
    # fixture put there (owner + the accepted member), none of
    # the spreadsheet's people are on it, and no Kitchen Crew
    # exists.
    members = client.get(
        f"/api/v1/groups/{gid}/members", headers=bearer(world["owner"])
    ).json()
    assert {m["email"] for m in members} == {
        world["owner"]["email"], world["member"]["email"],
    }
    golden_addresses = {
        "ana@example.org", "ben@example.org", "cy@example.org",
        "eli@example.org", "frank@example.org",
    }
    assert not golden_addresses & {m["email"] for m in members}
    subgroups = client.get(
        f"/api/v1/groups/{gid}/subgroups", headers=bearer(world["owner"])
    ).json()
    assert subgroups == []


def test_import_survives_the_messy_golden(client, world):
    gid = world["group"]["id"]
    response = _import(client, world)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["dry_run"] is False
    _check_messy_report(report)

    members = client.get(
        f"/api/v1/groups/{gid}/members", headers=bearer(world["owner"])
    ).json()
    by_email = {m["email"]: m for m in members}
    for email in (
        "ana@example.org", "ben@example.org", "cy@example.org",
        "eli@example.org", "frank@example.org",
    ):
        assert by_email[email]["status"] == "invited"
    assert "not-an-email" not in by_email
    assert "dana" not in " ".join(by_email)

    # Kitchen Crew holds exactly the three who named it.
    subgroups = client.get(
        f"/api/v1/groups/{gid}/subgroups", headers=bearer(world["owner"])
    ).json()
    assert [s["name"] for s in subgroups] == ["Kitchen Crew"]
    crew = client.get(
        f"/api/v1/groups/{gid}/subgroups/{subgroups[0]['id']}/members",
        headers=bearer(world["owner"]),
    ).json()
    assert {row["email"] for row in crew} == {
        "ana@example.org", "cy@example.org", "eli@example.org",
    }


def test_reimport_counts_members_instead_of_duplicating(client, world):
    _import(client, world)
    again = _import(client, world)
    assert again.status_code == 200, again.text
    report = again.json()
    assert report["created_users"] == 0
    assert report["memberships_created"] == 0
    assert report["enrolments"] == 0
    assert report["already_members"] == 5
    assert report["subgroups_created"] == []
    assert [e["row"] for e in report["errors"]] == [7]


def test_export_round_trips_the_comma_name(client, world):
    gid = world["group"]["id"]
    _import(client, world)

    as_json = client.get(
        f"/api/v1/groups/{gid}/members/export",
        headers=bearer(world["owner"]),
    )
    assert as_json.status_code == 200
    rows = {row["email"]: row for row in as_json.json()}
    assert rows["cy@example.org"]["name"] == "Carter, Cy"
    assert rows["cy@example.org"]["subgroups"] == ["Kitchen Crew"]
    assert rows["ben@example.org"]["subgroups"] == []
    assert rows["ana@example.org"]["status"] == "invited"

    as_csv = client.get(
        f"/api/v1/groups/{gid}/members/export?format=csv",
        headers=bearer(world["owner"]),
    )
    assert as_csv.status_code == 200
    assert as_csv.headers["content-type"].startswith("text/csv")
    parsed = list(csv.reader(io.StringIO(as_csv.text)))
    assert parsed[0] == ["name", "email", "role", "status", "sub-groups"]
    cy = next(row for row in parsed if "cy@example.org" in row)
    assert cy[0] == "Carter, Cy"  # the comma survived, quoted
    assert cy[4] == "Kitchen Crew"


def test_import_and_export_are_admin_doors(client, world):
    gid = world["group"]["id"]
    refused_import = _import(client, world, who=world["member"])
    assert refused_import.status_code == 403
    refused_export = client.get(
        f"/api/v1/groups/{gid}/members/export",
        headers=bearer(world["member"]),
    )
    assert refused_export.status_code == 403
