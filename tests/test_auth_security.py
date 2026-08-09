from __future__ import annotations

from sqlalchemy import select

from app.models import AuthSession, User


def test_signup_normalizes_unicode_rejects_duplicates_and_never_grants_admin(client, sqlite_session):
    res = client.post("/api/signup", json={"username": "  Ａ 학생  ", "password": "비밀번호"})
    assert res.status_code == 201
    assert res.json()["user"]["username"] == "A 학생"
    assert res.json()["user"]["is_admin"] is False

    duplicate = client.post("/api/signup", json={"username": "A 학생", "password": "x"})
    assert duplicate.status_code == 409
    assert "이미 사용 중" in duplicate.json()["detail"]

    bad = client.post("/api/signup", json={"username": "bad\nname", "password": "x"})
    assert bad.status_code == 422
    assert "제어 문자" in bad.json()["detail"]

    user = sqlite_session.scalar(select(User).where(User.username_normalized == "a 학생"))
    assert user is not None
    assert user.username == "A 학생"
    assert user.password_hash != "비밀번호"
    assert "$argon2id$" in user.password_hash
    assert user.is_admin is False


def test_username_normalized_key_collides_case_and_fullwidth_but_preserves_display(client, sqlite_session):
    first = client.post("/api/signup", json={"username": "  Ｆｏｏ Bar!  ", "password": "pw"})
    assert first.status_code == 201
    assert first.json()["user"]["username"] == "Foo Bar!"

    duplicate_case = client.post("/api/signup", json={"username": "foo bar!", "password": "pw"})
    assert duplicate_case.status_code == 409

    user = sqlite_session.scalar(select(User).where(User.username_normalized == "foo bar!"))
    assert user is not None
    assert user.username == "Foo Bar!"


def test_signup_rate_limit_uses_trusted_client_ip_and_returns_korean_429(client):
    headers = {"X-Forwarded-For": "203.0.113.77"}
    for idx in range(8):
        res = client.post("/api/signup", headers=headers, json={"username": f"rapid-{idx}", "password": "pw"})
        assert res.status_code == 201

    limited = client.post(
        "/api/signup", headers=headers, json={"username": "rapid-limited", "password": "pw"}
    )
    assert limited.status_code == 429
    assert "가입 시도가 많습니다" in limited.json()["detail"]

    other_ip = client.post(
        "/api/signup",
        headers={"X-Forwarded-For": "203.0.113.88"},
        json={"username": "rapid-other-ip", "password": "pw"},
    )
    assert other_ip.status_code == 201


def test_session_cookie_stores_only_token_hash_and_csrf_guards_mutations(client, sqlite_session):
    res = client.post("/api/signup", json={"username": "csrf-user", "password": "pw"})
    assert res.status_code == 201
    cookie = res.headers["set-cookie"]
    assert "mm70_session=" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "csrf_token" in res.json()

    db_session = sqlite_session.scalar(select(AuthSession))
    assert db_session is not None
    assert len(db_session.token_hash) == 64
    assert db_session.token_hash not in cookie

    no_csrf = client.post("/api/logout")
    assert no_csrf.status_code == 403
    assert "CSRF" in no_csrf.json()["detail"]

    ok = client.post("/api/logout", headers={"X-CSRF-Token": res.json()["csrf_token"]})
    assert ok.status_code == 204
    assert client.get("/api/me").status_code == 401


def test_auth_rate_limit_returns_korean_error_after_repeated_failures(client):
    client.post("/api/signup", json={"username": "rate-user", "password": "pw"})
    for _ in range(8):
        client.post("/api/login", json={"username": "rate-user", "password": "wrong"})
    limited = client.post("/api/login", json={"username": "rate-user", "password": "wrong"})
    assert limited.status_code == 429
    assert "잠시 후" in limited.json()["detail"]
