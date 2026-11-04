#!/usr/bin/env python3
"""Admin CLI.

  python scripts/manage.py create-superadmin --email you@company.com --name "Your Name"
  python scripts/manage.py reset-password --email you@company.com
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from sqlalchemy import select  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import AdminUser  # noqa: E402
from app.security import generate_password, hash_password  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-superadmin")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="Super Admin")
    c.add_argument("--password", help="omit to generate a strong temporary password")
    r = sub.add_parser("reset-password")
    r.add_argument("--email", required=True)
    a = p.parse_args()

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        email = a.email.strip().lower()
        user = db.scalar(select(AdminUser).where(AdminUser.email == email))
        pw = getattr(a, "password", None) or generate_password()
        if a.cmd == "create-superadmin":
            if user:
                user.role, user.is_active = "super_admin", True
            else:
                user = AdminUser(email=email, name=a.name, role="super_admin")
                db.add(user)
        elif not user:
            sys.exit(f"No user {email}")
        user.password_hash = hash_password(pw)
        user.must_change_password = True
        user.failed_logins, user.locked_until = 0, None
        user.token_version = (user.token_version or 0) + 1
        db.commit()
        print(f"OK  {email}  role={user.role}\nTemporary password: {pw}\n(You'll be asked to change it at first login.)")


if __name__ == "__main__":
    main()
