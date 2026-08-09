from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.cli import bootstrap_admin
from app.config import Settings
from app.db import Base, get_db
from app.importer import import_bundle
from app.main import create_app
from app.models import Problem, ProblemVersion, User
from tests.conftest import require_postgres_url, signup_and_login


def test_admin_rbac_and_bootstrap_cli(client, sqlite_session):
    auth = signup_and_login(client, "normal", "pw")
    denied = client.post(
        "/api/admin/import",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"path": "content/bundles/math70-v3-hard.json", "dry_run": True},
    )
    assert denied.status_code == 403

    created = bootstrap_admin(sqlite_session, username="root admin", password="pw")
    assert created["created"] is True
    again = bootstrap_admin(sqlite_session, username="root admin", password="pw2")
    assert again["created"] is False

    login = client.post("/api/login", json={"username": "root admin", "password": "pw"})
    assert login.status_code == 200
    ok = client.post(
        "/api/admin/import",
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
        json={"path": "content/bundles/math70-v3-hard.json", "dry_run": True},
    )
    assert ok.status_code == 200
    assert ok.json()["valid"] is True


def test_admin_problem_exam_versioning_validation_and_attempt_snapshots(client, sqlite_session):
    signup_and_login(client, "regular-user", "pw")
    assert client.get("/api/admin/problems").status_code == 403

    bootstrap_admin(sqlite_session, username="root admin 2", password="pw")
    admin_login = client.post("/api/login", json={"username": "root admin 2", "password": "pw"})
    csrf = admin_login.json()["csrf_token"]

    bad_level = client.post(
        "/api/admin/problems",
        headers={"X-CSRF-Token": csrf},
        json={
            "external_key": "admin-bad",
            "title": "bad",
            "level": 31,
            "base_xp": 10,
            "body_html": "<p>bad</p>",
            "answer_type": "choice",
            "choices": ["a"],
            "answer_spec": {"correct_index": 0},
        },
    )
    assert bad_level.status_code == 422

    created = client.post(
        "/api/admin/problems",
        headers={"X-CSRF-Token": csrf},
        json={
            "external_key": "admin-p1",
            "title": "<img src=x onerror=alert(1)>관리 문제",
            "grade": "중2",
            "semester": "1",
            "unit": "<script>bad()</script>일차함수",
            "tags": ["<b>tag</b>"],
            "level": 5,
            "base_xp": 20,
            "body_html": "<p>1+1?</p><script>bad()</script>",
            "answer_type": "choice",
            "choices": ["1", "2"],
            "answer_spec": {"correct_index": 1},
            "explanation_html": "<p>2</p>",
            "diagram_svg": "",
        },
    )
    assert created.status_code == 201, created.text
    problem = created.json()["problem"]
    old_version_id = problem["current_version_id"]
    assert problem["title"] == "<img src=x onerror=alert(1)>관리 문제"

    problems = client.get("/api/admin/problems").json()["problems"]
    assert any(row["id"] == problem["id"] and row["state"] == "draft" for row in problems)

    client.post(
        f"/api/admin/problems/{problem['id']}/publish",
        headers={"X-CSRF-Token": csrf},
        json={"version_id": old_version_id},
    )
    exam_created = client.post(
        "/api/admin/exams",
        headers={"X-CSRF-Token": csrf},
        json={"slug": "admin-exam", "title": "관리 시험", "time_limit_seconds": 120, "state": "draft"},
    )
    assert exam_created.status_code == 201
    exam_id = exam_created.json()["exam"]["id"]

    dup_seq = client.post(
        f"/api/admin/exams/{exam_id}/versions",
        headers={"X-CSRF-Token": csrf},
        json={
            "title": "bad",
            "time_limit_seconds": 120,
            "items": [
                {"sequence": 1, "problem_version_id": old_version_id, "points": 5},
                {"sequence": 1, "problem_version_id": old_version_id, "points": 5},
            ],
        },
    )
    assert dup_seq.status_code == 422

    missing_ref = client.post(
        f"/api/admin/exams/{exam_id}/versions",
        headers={"X-CSRF-Token": csrf},
        json={
            "title": "bad",
            "time_limit_seconds": 120,
            "items": [
                {"sequence": 1, "problem_version_id": "00000000-0000-0000-0000-000000000000", "points": 5}
            ],
        },
    )
    assert missing_ref.status_code == 422

    exam_version = client.post(
        f"/api/admin/exams/{exam_id}/versions",
        headers={"X-CSRF-Token": csrf},
        json={
            "title": "관리 시험 v1",
            "time_limit_seconds": 120,
            "items": [{"sequence": 1, "problem_version_id": old_version_id, "points": 5}],
        },
    )
    assert exam_version.status_code == 201, exam_version.text
    client.post(
        f"/api/admin/exams/{exam_id}/publish",
        headers={"X-CSRF-Token": csrf},
        json={"version_id": exam_version.json()["exam_version"]["id"]},
    )

    # User session was replaced by admin login in the shared client, so log in again.
    user_login = client.post("/api/login", json={"username": "regular-user", "password": "pw"})
    start = client.post(
        "/api/exams/admin-exam/attempts", headers={"X-CSRF-Token": user_login.json()["csrf_token"]}
    )
    assert start.status_code == 201
    attempt_id = start.json()["attempt_id"]
    snapshot_title = client.get(f"/api/attempts/{attempt_id}").json()["items"][0]["title"]

    admin_login = client.post("/api/login", json={"username": "root admin 2", "password": "pw"})
    csrf = admin_login.json()["csrf_token"]
    new_version = client.post(
        f"/api/admin/problems/{problem['id']}/versions",
        headers={"X-CSRF-Token": csrf},
        json={
            "title": "관리 문제 개정판",
            "body_html": "<p>2+2?</p>",
            "answer_type": "choice",
            "choices": ["3", "4"],
            "answer_spec": {"correct_index": 1},
            "explanation_html": "<p>4</p>",
            "diagram_svg": "",
        },
    )
    assert new_version.status_code == 201
    assert new_version.json()["problem_version"]["id"] != old_version_id
    old_version = sqlite_session.get(ProblemVersion, old_version_id)
    assert old_version.title == "<img src=x onerror=alert(1)>관리 문제"

    publish_new = client.post(
        f"/api/admin/problems/{problem['id']}/publish",
        headers={"X-CSRF-Token": csrf},
        json={"version_id": new_version.json()["problem_version"]["id"]},
    )
    assert publish_new.status_code == 200
    client.post("/api/login", json={"username": "regular-user", "password": "pw"})
    assert client.get(f"/api/attempts/{attempt_id}").json()["items"][0]["title"] == snapshot_title

    js = client.get("/static/app.js").text
    assert "alert(" not in js
    assert "confirm(" not in js


@pytest.mark.postgres
def test_real_postgresql_import_and_auth_boundaries():
    database_url = require_postgres_url()
    engine = create_engine(database_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(database_url=database_url, secret_key="pg-test-secret", secure_cookies=False)
    app = create_app(settings)
    with maker() as session:
        import_bundle(session, "content/bundles/math70-v3-hard.json", dry_run=False)

    def override_db():
        with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        assert client.get("/api/problems").status_code == 401
        signup = client.post("/api/signup", json={"username": "pg-user", "password": "pw"})
        assert signup.status_code == 201
        assert client.get("/api/problems").status_code == 200
        assert client.get("/api/exams").json()["exams"][0]["slug"] == "math70-v3-hard"

    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from problems")).scalar_one() == 25


@pytest.mark.postgres
def test_real_postgresql_concurrent_first_solve_is_exactly_once():
    database_url = require_postgres_url()
    engine = create_engine(database_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(database_url=database_url, secret_key="pg-race-secret", secure_cookies=False)
    app = create_app(settings)

    with maker() as session:
        import_bundle(session, "content/bundles/math70-v3-hard.json", dry_run=False)

    def override_db():
        with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        assert c.post("/api/signup", json={"username": "race-login", "password": "pw"}).status_code == 201
        problem = c.get("/api/problems").json()["problems"][0]
        version_id = problem["problem_version_id"]

    with maker() as session:
        version = session.get(ProblemVersion, version_id)
        correct = version.answer_spec["correct_index"]
        problem_id = version.problem_id

    def submit_once(key: str) -> int:
        with TestClient(app) as c:
            login = c.post("/api/login", json={"username": "race-login", "password": "pw"})
            res = c.post(
                f"/api/problems/{problem_id}/submit",
                headers={"X-CSRF-Token": login.json()["csrf_token"]},
                json={"answer": {"choice": correct}, "idempotency_key": key},
            )
            assert res.status_code == 200, res.text
            return res.json()["xp_awarded"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        awards = list(pool.map(submit_once, [f"race-{idx}" for idx in range(6)]))

    assert sum(1 for amount in awards if amount > 0) == 1
    with maker() as session:
        assert session.execute(text("select count(*) from problem_solves")).scalar_one() == 1
        assert session.execute(text("select count(*) from xp_ledger")).scalar_one() == 1
        login_user = session.scalar(select(User).where(User.username_normalized == "race-login"))
        assert login_user.total_xp == sum(awards)


@pytest.mark.e2e
@pytest.mark.parametrize(("width", "height"), [(390, 844), (1440, 1000)])
def test_browser_signup_gate_core_flows_admin_rbac_and_responsive_overflow(
    live_server_url, sqlite_session, width, height
):
    bootstrap_admin(sqlite_session, username="browser admin", password="pw")
    first_problem = sqlite_session.scalar(
        select(Problem).where(Problem.state == "published").order_by(Problem.level, Problem.external_key)
    )
    correct_choice = sqlite_session.get(ProblemVersion, first_problem.current_version_id).answer_spec[
        "correct_index"
    ]
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--window-size={width},{height}")
    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 8)

    def click(selector: str):
        item = wait.until(lambda d: d.find_element(By.CSS_SELECTOR, selector))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", item)
        return item

    def has_text(text: str):
        return wait.until(lambda d: text in d.find_element(By.TAG_NAME, "body").text)

    def autosave_complete():
        def state_or_error(driver):
            status = driver.find_element(By.ID, "saveStatus")
            if "저장 실패" in status.text:
                raise AssertionError(f"autosave failed: {status.text!r}")
            return status.text == "자동 저장 완료"

        return wait.until(state_or_error)

    try:
        driver.get(live_server_url + "/app")
        has_text("로그인")
        driver.get(live_server_url + "/")
        driver.find_element("name", "username").send_keys("브라우저 학생")
        driver.find_element("name", "password").send_keys("pw")
        click("button[data-signup]")
        has_text("오늘의 학습실")
        assert not driver.find_elements(By.CSS_SELECTOR, "button[data-view='admin']:not([hidden])")

        click("button[data-view='problems']")
        has_text("미해결")
        click(".problem-card")
        has_text("채점하기")
        click(f"[data-choice='{correct_choice}']")
        click("#submitPractice")
        has_text("정답입니다")

        click("button[data-view='problems']")
        has_text("해결")
        status_filter = driver.find_element(By.ID, "f_status")
        status_filter.send_keys("해결")
        click("#applyFilters")
        has_text("해결")

        click("button[data-view='exams']")
        has_text("모의고사")
        click("[data-exam='math70-v3-hard']")
        has_text("제출 검토")
        timer = wait.until(lambda d: d.find_element(By.ID, "timer"))
        assert "응시 시간이 종료되었습니다." not in driver.find_element(By.TAG_NAME, "body").text
        assert timer.text != "0:00"
        assert not driver.find_elements(By.ID, "save")
        click("[data-choice='1']")
        autosave_complete()
        click("#flagBox")
        autosave_complete()
        driver.refresh()
        has_text("제출 검토")
        assert (
            driver.find_element(By.CSS_SELECTOR, "[data-choice='1']").get_attribute("class").find("selected")
            >= 0
        )
        assert driver.find_element(By.ID, "flagBox").is_selected()
        click("#submitExam")
        has_text("미응답")
        has_text("다시 볼 문제")
        click("[data-final-submit]")
        has_text("채점이 끝났습니다")

        click("button[data-view='profile']")
        has_text("학습 기록")
        has_text("응시 기록")
        click("button[data-view='leaderboard']")
        has_text("학습 순위")
        has_text("브라우저 학생")

        click("#logoutBtn")
        driver.get(live_server_url + "/")
        driver.find_element("name", "username").send_keys("browser admin")
        driver.find_element("name", "password").send_keys("pw")
        click("button[data-login]")
        has_text("오늘의 학습실")
        assert driver.find_elements(By.CSS_SELECTOR, "button[data-view='admin']:not([hidden])")
        click("button[data-view='admin']")
        has_text("콘텐츠 관리")
        has_text("번들 가져오기")

        assert driver.execute_script(
            "return document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
    finally:
        driver.quit()
