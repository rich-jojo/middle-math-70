from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.db import utcnow
from app.importer import import_bundle, load_bundle
from app.models import Attempt, AttemptAnswer, Problem, ProblemVersion, User, XpLedger
from tests.conftest import signup_and_login


def test_v3_bundle_is_the_only_published_content_contract(client):
    bundle = load_bundle("content/bundles/math70-v3-hard.json")
    assert len(bundle["problems"]) == 25
    assert [p["external_key"] for p in bundle["problems"][:3]] == [
        "math70-v3-hard-001",
        "math70-v3-hard-002",
        "math70-v3-hard-003",
    ]
    assert bundle["problems"][0]["title"] == "1. 중2-2 마름모와 피타고라스 정리"
    assert bundle["problems"][-1]["external_key"] == "math70-v3-hard-025"
    assert bundle["exams"][0]["slug"] == "math70-v3-hard"
    assert len(bundle["exams"][0]["items"]) == 25

    assert client.head("/middle-math-70-exam.pdf").status_code == 404
    assert client.head("/middle-math-70-solutions.pdf").status_code == 404


def test_bundle_import_is_idempotent_and_creates_immutable_exam_versions(client, sqlite_session):
    first = import_bundle(sqlite_session, "content/bundles/math70-v3-hard.json", dry_run=False)
    second = import_bundle(sqlite_session, "content/bundles/math70-v3-hard.json", dry_run=False)
    signup_and_login(client, "bundle-reader", "pw")

    assert first["created_problems"] == 25
    assert second["created_problems"] == 0
    assert (
        sqlite_session.scalar(select(Problem).where(Problem.external_key == "math70-v3-hard-001")) is not None
    )
    assert len(client.get("/api/exams").json()["exams"]) == 1


def test_problem_detail_hides_answer_until_submit_and_practice_awards_xp_once(client, sqlite_session):
    import_bundle(sqlite_session, "content/bundles/math70-v3-hard.json", dry_run=False)
    auth = signup_and_login(client, "solver", "pw")

    problems = client.get("/api/problems").json()["problems"]
    problem_id = problems[0]["id"]
    detail = client.get(f"/api/problems/{problem_id}").json()
    assert "answer_spec" not in detail
    assert "explanation_html" not in detail

    wrong = client.post(
        f"/api/problems/{problem_id}/submit",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answer": {"choice": 0}, "idempotency_key": "p1-wrong"},
    )
    assert wrong.status_code == 200
    assert wrong.json()["correct"] is False
    assert wrong.json()["xp_awarded"] == 0
    assert "explanation_html" in wrong.json()

    correct_index = sqlite_session.scalar(
        select(ProblemVersion).where(ProblemVersion.problem_id == problem_id)
    ).answer_spec["correct_index"]
    ok1 = client.post(
        f"/api/problems/{problem_id}/submit",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answer": {"choice": correct_index}, "idempotency_key": "p1-correct"},
    )
    ok2 = client.post(
        f"/api/problems/{problem_id}/submit",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answer": {"choice": correct_index}, "idempotency_key": "p1-correct"},
    )
    assert ok1.json()["xp_awarded"] == detail["base_xp"]
    assert ok2.json()["xp_awarded"] == 0
    assert (
        sqlite_session.scalar(select(User).where(User.username_normalized == "solver")).total_xp
        == detail["base_xp"]
    )
    assert len(sqlite_session.scalars(select(XpLedger)).all()) == 1


def test_problem_list_returns_solved_state_and_filters_for_current_user(client, sqlite_session):
    import_bundle(sqlite_session, "content/bundles/math70-v3-hard.json", dry_run=False)
    auth = signup_and_login(client, "filter-user", "pw")

    initial = client.get("/api/problems").json()["problems"]
    assert len(initial) == 25
    assert {p["solved"] for p in initial} == {False}

    target = initial[0]
    version = sqlite_session.get(ProblemVersion, target["problem_version_id"])
    submit = client.post(
        f"/api/problems/{target['id']}/submit",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answer": {"choice": version.answer_spec["correct_index"]}, "idempotency_key": "solve-filter"},
    )
    assert submit.status_code == 200

    solved = client.get("/api/problems?status=solved").json()["problems"]
    unsolved = client.get("/api/problems?status=unsolved").json()["problems"]
    assert [p["id"] for p in solved] == [target["id"]]
    assert solved[0]["solved"] is True
    assert target["id"] not in {p["id"] for p in unsolved}
    assert all(p["solved"] is False for p in unsolved)


def test_profile_endpoint_reports_xp_solve_count_and_attempt_history(client, sqlite_session):
    import_bundle(sqlite_session, "content/bundles/math70-v3-hard.json", dry_run=False)
    auth = signup_and_login(client, "profile-user", "pw")
    problem = client.get("/api/problems").json()["problems"][0]
    version = sqlite_session.get(ProblemVersion, problem["problem_version_id"])
    client.post(
        f"/api/problems/{problem['id']}/submit",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answer": {"choice": version.answer_spec["correct_index"]}, "idempotency_key": "profile-solve"},
    )
    start = client.post("/api/exams/math70-v3-hard/attempts", headers={"X-CSRF-Token": auth["csrf"]})
    assert start.status_code == 201

    profile = client.get("/api/profile").json()
    assert profile["user"]["username"] == "profile-user"
    assert profile["solve_count"] == 1
    assert profile["user"]["total_xp"] == problem["base_xp"]
    assert profile["attempts"][0]["id"] == start.json()["attempt_id"]


def test_exam_attempt_freezes_snapshot_autosaves_grades_and_does_not_double_award_xp(client, sqlite_session):
    import_bundle(sqlite_session, "content/bundles/math70-v3-hard.json", dry_run=False)
    auth = signup_and_login(client, "exam-user", "pw")

    start = client.post("/api/exams/math70-v3-hard/attempts", headers={"X-CSRF-Token": auth["csrf"]})
    assert start.status_code == 201
    attempt_id = start.json()["attempt_id"]
    attempt = client.get(f"/api/attempts/{attempt_id}").json()
    assert "answer_spec" not in str(attempt)
    assert len(attempt["items"]) == 25

    first_problem_id = attempt["items"][0]["problem_id"]
    old_title = attempt["items"][0]["title"]
    problem = sqlite_session.get(Problem, first_problem_id)
    problem.title = "나중에 바뀐 제목"
    sqlite_session.commit()
    resumed = client.get(f"/api/attempts/{attempt_id}").json()
    assert resumed["items"][0]["title"] == old_title

    answers = {}
    for item in attempt["items"]:
        version = sqlite_session.get(ProblemVersion, item["problem_version_id"])
        if version.answer_type == "choice":
            answers[item["sequence"]] = {"choice": version.answer_spec["correct_index"]}
        else:
            answers[item["sequence"]] = {"text": version.answer_spec["accepted"][0]}

    save = client.patch(
        f"/api/attempts/{attempt_id}/answers",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answers": {"1": answers[1]}, "flags": {"1": True}},
    )
    assert save.status_code == 200
    assert client.get(f"/api/attempts/{attempt_id}").json()["answers"]["1"] == answers[1]

    submit1 = client.post(
        f"/api/attempts/{attempt_id}/submit",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answers": answers, "idempotency_key": "exam-submit"},
    )
    submit2 = client.post(
        f"/api/attempts/{attempt_id}/submit",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answers": answers, "idempotency_key": "exam-submit"},
    )
    assert submit1.json()["score"] == 100
    assert submit1.json()["xp_awarded"] > 0
    assert submit2.json()["xp_awarded"] == submit1.json()["xp_awarded"]
    assert submit2.json()["review"] == submit1.json()["review"]
    assert client.get("/api/leaderboard").json()["users"][0]["username"] == "exam-user"
    assert len(sqlite_session.scalars(select(XpLedger)).all()) == 25

    rejected_save = client.patch(
        f"/api/attempts/{attempt_id}/answers",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answers": {"1": {"choice": 0}}, "flags": {}},
    )
    assert rejected_save.status_code == 409

    changed_answer = dict(answers)
    changed_answer[1] = {"choice": 0}
    changed = client.post(
        f"/api/attempts/{attempt_id}/submit",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answers": changed_answer, "idempotency_key": "exam-submit"},
    )
    assert changed.status_code == 409

    different_key = client.post(
        f"/api/attempts/{attempt_id}/submit",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answers": answers, "idempotency_key": "exam-submit-2"},
    )
    assert different_key.status_code == 409


def test_attempt_save_and_submit_validate_sequences_and_deadline(client, sqlite_session):
    import_bundle(sqlite_session, "content/bundles/math70-v3-hard.json", dry_run=False)
    auth = signup_and_login(client, "deadline-user", "pw")
    start = client.post("/api/exams/math70-v3-hard/attempts", headers={"X-CSRF-Token": auth["csrf"]})
    attempt_id = start.json()["attempt_id"]
    attempt_payload = client.get(f"/api/attempts/{attempt_id}").json()
    assert attempt_payload["started_at"]
    assert attempt_payload["deadline_at"]

    invalid_save = client.patch(
        f"/api/attempts/{attempt_id}/answers",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answers": {"26": {"choice": 0}}, "flags": {}},
    )
    assert invalid_save.status_code == 422

    invalid_flag = client.patch(
        f"/api/attempts/{attempt_id}/answers",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answers": {}, "flags": {"0": True}},
    )
    assert invalid_flag.status_code == 422

    attempt = sqlite_session.get(Attempt, attempt_id)
    attempt.deadline_at = utcnow() - timedelta(seconds=1)
    sqlite_session.commit()
    expired_save = client.patch(
        f"/api/attempts/{attempt_id}/answers",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answers": {"1": {"choice": 0}}, "flags": {}},
    )
    assert expired_save.status_code == 409
    assert "시간" in expired_save.json()["detail"]

    expired_submit = client.post(
        f"/api/attempts/{attempt_id}/submit",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answers": {"1": {"choice": 0}}, "idempotency_key": "expired"},
    )
    assert expired_submit.status_code == 409
    assert sqlite_session.scalars(select(AttemptAnswer)).all() == []
