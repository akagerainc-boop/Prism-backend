"""Create (or reset the password of) an admin-dashboard login.

There's no public admin signup -- this is the only way to create one:

    python seed_admin.py admin@example.com --name "Your Name"

Prompts for a password (hidden input), hashes it, and upserts the
``admin_users`` row. Safe to re-run for an existing email to reset its
password.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.db import session_scope
from app.models import AdminUser
from app.security import hash_admin_password, is_valid_email, normalize_email


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--name", default=None, help="Display name shown in the dashboard.")
    args = parser.parse_args()

    email = normalize_email(args.email)
    if not is_valid_email(email):
        print(f"'{args.email}' doesn't look like a valid email address.", file=sys.stderr)
        raise SystemExit(1)

    password = getpass.getpass("Password: ")
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        raise SystemExit(1)
    if password != getpass.getpass("Confirm password: "):
        print("Passwords didn't match.", file=sys.stderr)
        raise SystemExit(1)

    with session_scope() as db:
        admin = db.query(AdminUser).filter(AdminUser.email == email).first()
        if admin is None:
            admin = AdminUser(email=email)
            action = "Created"
        else:
            action = "Updated"

        admin.password_hash = hash_admin_password(password)
        admin.display_name = args.name
        admin.is_active = True
        db.add(admin)

    print(f"{action} admin login for {email}.")


if __name__ == "__main__":
    main()
