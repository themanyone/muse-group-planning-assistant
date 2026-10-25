"""Seed chassis (roadmap B-009).

Seed files are JSON in ``seeds/``. Loading is IDEMPOTENT by natural
key — users by email, groups by slug — so running the loader twice
changes nothing the second time; that property is what makes seeds
safe in demos, tests, and fresh installs alike.
"""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from gpa.models.groups import Group, Membership
from gpa.models.identity import User
from gpa.security import hash_password


def load_seed_data(db: Session, data: dict) -> dict:
    """Apply one seed document; return what was created vs present.

    Users: ``{email, password?, name?, timezone?}`` — a seed password
    is demo data printed in the repo on purpose, never a real secret.
    Groups: ``{name, owner_email, visibility?, home_timezone?,
    default_place?, description?}`` — the owner gets an active owner
    membership.
    """
    created = {"users": 0, "groups": 0}
    present = {"users": 0, "groups": 0}

    users_by_email: dict[str, User] = {}
    for spec in data.get("users", []):
        user = db.query(User).filter(User.email == spec["email"]).one_or_none()
        if user is None:
            user = User(
                email=spec["email"],
                name=spec.get("name", ""),
                password_hash=hash_password(spec["password"]) if spec.get("password") else None,
                timezone=spec.get("timezone", "UTC"),
                locale=spec.get("locale", "en"),
            )
            db.add(user)
            db.flush()
            created["users"] += 1
        else:
            present["users"] += 1
        users_by_email[spec["email"]] = user

    for spec in data.get("groups", []):
        from gpa.services.groups import slugify

        slug = spec.get("slug") or slugify(spec["name"])
        group = db.query(Group).filter(Group.slug == slug).one_or_none()
        if group is None:
            group = Group(
                name=spec["name"],
                slug=slug,
                description=spec.get("description", ""),
                visibility=spec.get("visibility", "private"),
                home_timezone=spec.get("home_timezone", "UTC"),
                default_place=spec.get("default_place", ""),
            )
            db.add(group)
            db.flush()
            created["groups"] += 1
        else:
            present["groups"] += 1
        owner = users_by_email.get(spec["owner_email"]) or db.query(User).filter(
            User.email == spec["owner_email"]
        ).one_or_none()
        if owner is not None:
            membership = (
                db.query(Membership)
                .filter(Membership.user_id == owner.id, Membership.group_id == group.id)
                .one_or_none()
            )
            if membership is None:
                db.add(
                    Membership(user_id=owner.id, group_id=group.id, role="owner", status="active")
                )
    # Extra memberships (Phase 14, B-147): a seed may enrol
    # people beyond each group's owner —
    # ``{group_slug, user_email, role?}`` — idempotently, so the
    # demo story can carry a whole cast, not just owners.
    for spec in data.get("memberships", []):
        user = users_by_email.get(spec["user_email"]) or db.query(User).filter(
            User.email == spec["user_email"]
        ).one_or_none()
        group = db.query(Group).filter(Group.slug == spec["group_slug"]).one_or_none()
        if user is None or group is None:
            continue
        membership = (
            db.query(Membership)
            .filter(Membership.user_id == user.id, Membership.group_id == group.id)
            .one_or_none()
        )
        if membership is None:
            db.add(
                Membership(
                    user_id=user.id,
                    group_id=group.id,
                    role=spec.get("role", "member"),
                    status=spec.get("status", "active"),
                )
            )

    # Phase 7 (B-091): every seeded database also carries the
    # global starter templates (group_id NULL), idempotent like
    # everything else here. Their counts ride under their own
    # key so the users/groups counts stay the shape B-009 set.
    from gpa.services.templates import seed_starter_templates

    templates = seed_starter_templates(db)
    db.commit()
    return {"created": created, "already_present": present, "templates": templates}


def load_seed_file(db: Session, path: Path | str) -> dict:
    """Load one JSON seed file (see load_seed_data for the shape)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return load_seed_data(db, data)


def load_all_seeds(db: Session, seeds_dir: Path | str) -> dict:
    """Load every ``*.json`` in a seeds directory, in name order."""
    total = {"created": {"users": 0, "groups": 0}, "already_present": {"users": 0, "groups": 0}}
    for path in sorted(Path(seeds_dir).glob("*.json")):
        result = load_seed_file(db, path)
        for section in total:
            for key in total[section]:
                total[section][key] += result[section][key]
    return total
