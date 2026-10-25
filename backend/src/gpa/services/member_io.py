"""Member import/export (roadmap B-110).

The nonprofit's existing spreadsheet is the real input this
must survive: duplicate rows, blank lines, names with commas
in them ("Carter, Cy"), stray trailing columns, the odd bad
email. The importer reads CSV (name, email, sub-group) and
answers with a REPORT before it answers with changes — and a
dry run computes the whole report, then rolls its transaction
back, so "what would happen" is the truth, not a guess.

Semantics, plainly:

- an imported person becomes an INVITED member (the invite
  law: membership activates when the person accepts — the
  import never silently activates anyone) and is enrolled in
  the named sub-group when one is given;
- a person who already belongs is counted, never duplicated;
  their named enrolment still applies;
- a duplicate row inside the file is skipped and said so;
- a row whose email cannot be read is an error row, named by
  its spreadsheet row number, and changes nothing;
- the name column never overwrites a name a person already
  has — the spreadsheet is evidence, not identity.

Export is the mirror: CSV or JSON of the membership (name,
email, role, status, sub-groups), CSV written with real
quoting so a comma'd name round-trips.
"""

import csv
import io

from sqlalchemy.orm import Session

from gpa.errors import AppError
from gpa.models.groups import Group, Membership
from gpa.models.identity import User
from gpa.models.nonprofit import SubGroup, SubGroupMembership
from gpa.services import audit
from gpa.services.identity import get_user_by_email, normalize_email
from gpa.services import subgroups as subgroup_service


def _split_row(fields: list[str]) -> tuple[str, str, str]:
    """One CSV row → (name, email, sub-group name).

    The email is found by shape (it contains "@"), not by
    position — that single choice is what lets an unquoted
    "Carter, Cy" name survive its own comma, and lets stray
    trailing columns fall away instead of poisoning the row.
    With no email-shaped field, position decides and the
    validator judges the result.
    """
    fields = [field.strip() for field in fields]
    email_at = next((i for i, f in enumerate(fields) if "@" in f), None)
    if email_at is None:
        name = fields[0] if fields else ""
        email = fields[1] if len(fields) > 1 else ""
        subgroup = fields[2] if len(fields) > 2 else ""
        return name, email, subgroup
    name = ", ".join(part for part in fields[:email_at] if part)
    email = fields[email_at]
    subgroup = fields[email_at + 1] if len(fields) > email_at + 1 else ""
    return name, email, subgroup


def _is_header(fields: list[str]) -> bool:
    """The spreadsheet's header row: an "email" cell, in words."""
    return any(field.strip().lower() == "email" for field in fields)


def import_members(
    db: Session,
    *,
    actor_label: str,
    group: Group,
    csv_text: str,
    dry_run: bool = False,
) -> dict:
    """Import a membership CSV. Returns the report dict; on a
    dry run the identical work is done and then rolled back —
    the report describes exactly what a real run would do."""
    report = {
        "dry_run": dry_run,
        "rows_total": 0,
        "created_users": 0,
        "memberships_created": 0,
        "enrolments": 0,
        "already_members": 0,
        "blank_rows": 0,
        "subgroups_created": [],
        "skipped": [],
        "errors": [],
    }
    reader = csv.reader(io.StringIO(csv_text))
    seen_emails: set[str] = set()
    subgroups_by_name: dict[str, SubGroup] = {}
    first_row = True

    def subgroup_named(name: str) -> SubGroup:
        """Find-or-create a sub-group by name, once per import."""
        if name not in subgroups_by_name:
            row = (
                db.query(SubGroup)
                .filter(SubGroup.group_id == group.id, SubGroup.name == name)
                .one_or_none()
            )
            if row is None:
                row = SubGroup(
                    group_id=group.id, name=name,
                    description="", created_by=actor_label,
                )
                db.add(row)
                db.flush()
                report["subgroups_created"].append(name)
            subgroups_by_name[name] = row
        return subgroups_by_name[name]

    for row_number, fields in enumerate(reader, start=1):
        if not any(field.strip() for field in fields):
            report["blank_rows"] += 1
            continue
        if first_row and _is_header(fields):
            first_row = False
            continue
        first_row = False
        report["rows_total"] += 1
        name, email_raw, subgroup_name = _split_row(fields)
        try:
            email = normalize_email(email_raw)
        except AppError:
            report["errors"].append(
                {"row": row_number, "reason": f"email '{email_raw}' does not look right"}
            )
            continue
        if email in seen_emails:
            report["skipped"].append(
                {"row": row_number, "reason": "duplicate row in this file"}
            )
            continue
        seen_emails.add(email)

        user = get_user_by_email(db, email)
        if user is None:
            user = User(email=email, name=name[:160])
            db.add(user)
            db.flush()
            report["created_users"] += 1
        elif not user.name and name:
            # A name the person never had, the spreadsheet may
            # supply; a name they HAVE is theirs (see header).
            user.name = name[:160]

        membership = (
            db.query(Membership)
            .filter(Membership.user_id == user.id, Membership.group_id == group.id)
            .one_or_none()
        )
        if membership is None:
            db.add(
                Membership(
                    user_id=user.id, group_id=group.id,
                    role="member", status="invited",
                )
            )
            db.flush()
            report["memberships_created"] += 1
        elif membership.status == "declined":
            membership.status = "invited"
            report["memberships_created"] += 1
        else:
            report["already_members"] += 1

        if subgroup_name:
            subgroup = subgroup_named(subgroup_name[:120])
            before = subgroup_service.enrolment_of(db, subgroup.id, user.id)
            subgroup_service.ensure_enrolment(
                db, group=group, subgroup=subgroup, user_id=user.id
            )
            if before is None:
                report["enrolments"] += 1

    if dry_run:
        # The report is the deliverable; the work is not. The
        # audit row goes with the rollback — a dry run happened
        # to nobody.
        db.rollback()
        return report
    audit.record(
        db, actor=actor_label, action="members.imported",
        group_id=group.id, entity_type="group", entity_id=group.id,
        detail={
            "rows_total": report["rows_total"],
            "memberships_created": report["memberships_created"],
            "created_users": report["created_users"],
            "enrolments": report["enrolments"],
            "errors": len(report["errors"]),
            "skipped": len(report["skipped"]),
        },
    )
    db.commit()
    return report


def export_rows(db: Session, group: Group) -> list[dict]:
    """The membership as export rows: every membership (invited
    and active alike — the spreadsheet is the group's roster),
    name order, each with their sub-group names."""
    memberships = (
        db.query(Membership, User)
        .join(User, User.id == Membership.user_id)
        .filter(Membership.group_id == group.id)
        .all()
    )
    enrolments: dict[str, list[str]] = {}
    for row, subgroup in (
        db.query(SubGroupMembership, SubGroup)
        .join(SubGroup, SubGroup.id == SubGroupMembership.subgroup_id)
        .filter(SubGroupMembership.group_id == group.id)
        .all()
    ):
        enrolments.setdefault(row.user_id, []).append(subgroup.name)
    out = [
        {
            "name": user.name or "",
            "email": user.email,
            "role": membership.role,
            "status": membership.status,
            "subgroups": sorted(enrolments.get(user.id, [])),
        }
        for membership, user in memberships
    ]
    out.sort(key=lambda r: (r["name"].lower(), r["email"]))
    return out


def export_csv(db: Session, group: Group) -> str:
    """The roster as CSV — real quoting, so "Carter, Cy"
    survives the round trip home."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["name", "email", "role", "status", "sub-groups"])
    for row in export_rows(db, group):
        writer.writerow(
            [
                row["name"], row["email"], row["role"], row["status"],
                "; ".join(row["subgroups"]),
            ]
        )
    return buffer.getvalue()
