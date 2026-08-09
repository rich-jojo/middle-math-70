from __future__ import annotations

import os
import sys

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine
from app.importer import import_bundle
from app.models import Role, User, UserRole
from app.security import hash_password, normalize_username, username_key, validate_password

app = typer.Typer(no_args_is_help=True)


def bootstrap_admin(db: Session, username: str, password: str) -> dict:
    display = normalize_username(username)
    normalized = username_key(username)
    validate_password(password)
    admin_role = db.scalar(select(Role).where(Role.name == "admin"))
    if not admin_role:
        admin_role = Role(name="admin")
        db.add(admin_role)
        db.flush()
    existing = db.scalar(select(User).where(User.username_normalized == normalized))
    if existing:
        return {"created": False, "username": existing.username, "is_admin": existing.is_admin}
    user = User(
        username=display, username_normalized=normalized, password_hash=hash_password(password), is_admin=True
    )
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.id, role_id=admin_role.id))
    db.commit()
    return {"created": True, "username": user.username, "is_admin": True}


@app.command("bootstrap-admin")
def bootstrap_admin_command(
    username: str = typer.Option("", envvar="MM70_BOOTSTRAP_ADMIN_USERNAME"),
    password: str = typer.Option("", envvar="MM70_BOOTSTRAP_ADMIN_PASSWORD"),
) -> None:
    if not username:
        username = input("관리자 사용자 이름: ")
    if not password:
        password = (
            sys.stdin.readline().rstrip("\n")
            if not sys.stdin.isatty()
            else typer.prompt("관리자 비밀번호", hide_input=True)
        )
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        result = bootstrap_admin(db, username=username, password=password)
    typer.echo("created" if result["created"] else "exists")


@app.command("import-bundle")
def import_bundle_command(path: str, dry_run: bool = False) -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        result = import_bundle(db, path, dry_run=dry_run)
    typer.echo(result)


@app.command("init-db")
def init_db() -> None:
    Base.metadata.create_all(engine)
    bundle = os.getenv("MM70_AUTO_IMPORT_BUNDLE")
    if bundle:
        with SessionLocal() as db:
            typer.echo(import_bundle(db, bundle, dry_run=False))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
