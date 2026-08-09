from __future__ import annotations

import json
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db, utcnow
from app.grading import award_first_solve_xp, grade_points, is_correct
from app.importer import clean_html, digest, import_bundle
from app.models import (
    Attempt,
    AttemptAnswer,
    AuthSession,
    Exam,
    ExamVersion,
    ExamVersionItem,
    Problem,
    ProblemSolve,
    ProblemVersion,
    User,
)
from app.rating import tier_badge_svg, tier_for_level, tier_for_xp
from app.security import (
    check_rate_limit,
    check_signup_rate_limit,
    clear_login_failures,
    client_ip,
    create_session,
    get_current_session,
    get_current_user,
    hash_password,
    normalize_username,
    record_login_failure,
    require_admin,
    require_csrf,
    username_key,
    validate_password,
    verify_password,
)


class AuthIn(BaseModel):
    username: str
    password: str


class PracticeSubmitIn(BaseModel):
    answer: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str


class AttemptSaveIn(BaseModel):
    answers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    flags: dict[str, bool] = Field(default_factory=dict)


class AttemptSubmitIn(BaseModel):
    answers: dict[str, dict[str, Any]]
    idempotency_key: str


class ImportIn(BaseModel):
    path: str
    dry_run: bool = True


class ProblemVersionIn(BaseModel):
    title: str
    body_html: str
    answer_type: str
    choices: list[str] = Field(default_factory=list)
    answer_spec: dict[str, Any] = Field(default_factory=dict)
    explanation_html: str = ""
    diagram_svg: str = ""


class ProblemCreateIn(ProblemVersionIn):
    external_key: str
    grade: str = ""
    semester: str = ""
    unit: str = ""
    tags: list[str] = Field(default_factory=list)
    level: int
    base_xp: int
    state: str = "draft"


class PublishIn(BaseModel):
    version_id: str


class ExamCreateIn(BaseModel):
    slug: str
    title: str
    time_limit_seconds: int
    state: str = "draft"


class ExamItemIn(BaseModel):
    sequence: int
    problem_version_id: str
    points: int


class ExamVersionIn(BaseModel):
    title: str
    time_limit_seconds: int
    items: list[ExamItemIn]


def user_payload(user: User, csrf_token: str | None = None) -> dict:
    payload = {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "total_xp": user.total_xp,
        "tier": tier_for_xp(user.total_xp),
    }
    if csrf_token:
        payload["csrf_token"] = csrf_token
    return payload


def public_problem(version: ProblemVersion, problem: Problem, include_solution: bool = False) -> dict:
    data = {
        "id": problem.id,
        "problem_version_id": version.id,
        "external_key": problem.external_key,
        "title": version.title,
        "body_html": version.body_html,
        "diagram_svg": version.diagram_svg,
        "answer_type": version.answer_type,
        "choices": [{"index": idx, "text": text} for idx, text in enumerate(version.choices or [])],
        "level": problem.level,
        "tier": tier_for_level(problem.level),
        "tier_badge_svg": tier_badge_svg(problem.level),
        "base_xp": problem.base_xp,
        "grade": problem.grade,
        "semester": problem.semester,
        "unit": problem.unit,
        "tags": (problem.tags or {}).get("items", []),
        "state": problem.state,
    }
    if include_solution:
        data["answer_spec"] = version.answer_spec
        data["explanation_html"] = version.explanation_html
    return data


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def aware_utc(value: Any) -> Any:
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def validate_problem_meta(level: int, base_xp: int) -> None:
    if level < 1 or level > 30:
        raise HTTPException(status_code=422, detail="난이도는 1..30 범위여야 합니다.")
    if base_xp < 0:
        raise HTTPException(status_code=422, detail="XP는 0 이상이어야 합니다.")


def validate_problem_version_input(payload: ProblemVersionIn) -> None:
    if payload.answer_type not in {"choice", "text", "process"}:
        raise HTTPException(status_code=422, detail="지원하지 않는 답안 유형입니다.")
    if payload.answer_type == "choice":
        idx = payload.answer_spec.get("correct_index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(payload.choices):
            raise HTTPException(status_code=422, detail="객관식 정답 인덱스가 보기와 맞지 않습니다.")
    if payload.answer_type in {"text", "process"} and not payload.answer_spec.get("accepted"):
        raise HTTPException(status_code=422, detail="단답/과정형 accepted 답안이 필요합니다.")


def problem_version_payload(payload: ProblemVersionIn) -> dict:
    validate_problem_version_input(payload)
    return {
        "title": payload.title,
        "body_html": clean_html(payload.body_html),
        "answer_type": payload.answer_type,
        "choices": payload.choices,
        "answer_spec": payload.answer_spec,
        "explanation_html": clean_html(payload.explanation_html),
        "diagram_svg": clean_html(payload.diagram_svg),
    }


def attempt_sequences(attempt: Attempt) -> set[str]:
    return {str(item["sequence"]) for item in attempt.snapshot.get("items", [])}


def validate_attempt_mutation(
    attempt: Attempt, answers: dict[str, Any], flags: dict[str, bool] | None = None
) -> None:
    valid = attempt_sequences(attempt)
    submitted = set(str(k) for k in answers)
    submitted.update(str(k) for k in (flags or {}))
    if not submitted.issubset(valid):
        raise HTTPException(status_code=422, detail="응시 문항에 없는 번호입니다.")


def ensure_attempt_open(attempt: Attempt) -> None:
    if attempt.status != "in_progress":
        raise HTTPException(status_code=409, detail="이미 제출된 응시입니다.")
    if attempt.deadline_at and utcnow() > aware_utc(attempt.deadline_at):
        raise HTTPException(status_code=409, detail="응시 시간이 종료되었습니다.")


def build_attempt_snapshot(db: Session, exam_version: ExamVersion) -> dict:
    rows = db.scalars(
        select(ExamVersionItem)
        .where(ExamVersionItem.exam_version_id == exam_version.id)
        .order_by(ExamVersionItem.sequence)
    ).all()
    items = []
    for row in rows:
        version = row.problem_version
        problem = row.problem
        payload = public_problem(version, problem, include_solution=False)
        payload.update(
            {
                "sequence": row.sequence,
                "points": row.points,
                "problem_id": problem.id,
                "problem_version_id": version.id,
                "title": version.title,
            }
        )
        items.append(payload)
    return {
        "exam_version_id": exam_version.id,
        "title": exam_version.title,
        "time_limit_seconds": exam_version.time_limit_seconds,
        "items": items,
    }


def grade_attempt(
    db: Session, user: User, attempt: Attempt, answers: dict[str, dict[str, Any]]
) -> tuple[int, int, list[dict]]:
    score = 0
    xp = 0
    review = []
    for item in attempt.snapshot["items"]:
        seq = str(item["sequence"])
        version = db.get(ProblemVersion, item["problem_version_id"])
        points = grade_points(version, answers.get(seq, {}), item["points"])
        correct = points == item["points"]
        score += points
        if correct:
            xp += award_first_solve_xp(db, user, version, item["problem_id"], attempt_id=attempt.id)
        review.append(
            {
                "sequence": item["sequence"],
                "points": points,
                "correct": correct,
                "answer_spec": version.answer_spec,
                "explanation_html": version.explanation_html,
            }
        )
    return score, xp, review


LANDING_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>중등 수학 70 중앙 플랫폼</title><link rel="stylesheet" href="/static/app.css"></head>
<body><main class="auth-page"><section class="hero"><p class="eyebrow">중앙 문제은행 · 계정 기반 CBT</p>
<h1>중등 수학 70점 돌파</h1><p>문제 풀이 기록, 모의고사 저장, 첫 풀이 XP와 순위를 서버에 안전하게 저장합니다.</p>
<p><a href="/middle-math-70-exam.pdf">문제지 PDF</a> · <a href="/middle-math-70-solutions.pdf">해설 PDF</a></p></section>
<section class="auth-panel" aria-label="로그인과 가입"><h2>로그인 / 가입</h2>
<form id="authForm"><label>사용자 이름<input name="username" autocomplete="username" required maxlength="64"></label>
<label>비밀번호<input name="password" type="password" autocomplete="current-password" required maxlength="256"></label>
<div class="actions"><button type="submit" data-login>로그인</button><button type="button" data-signup>가입하기</button></div>
<p id="authStatus" role="status"></p></form></section></main><script src="/static/app.js"></script></body></html>"""


APP_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>수학 70 워크스테이션</title><link rel="stylesheet" href="/static/app.css"></head>
<body><div class="shell"><header class="topbar"><a class="brand" href="/app">수학 70</a>
<nav><button data-view="dashboard">대시보드</button><button data-view="problems">문제</button><button data-view="exams">모의고사</button><button data-view="profile">프로필</button><button data-view="leaderboard">순위</button><button data-view="admin" data-admin-only hidden>관리</button></nav>
<button id="logoutBtn">로그아웃</button></header><main id="appRoot" tabindex="-1"><p class="loading">불러오는 중</p></main></div>
<script src="/static/app.js"></script></body></html>"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Middle Math 70 Central", version="0.2.0")

    @app.get("/health")
    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def landing() -> str:
        return LANDING_HTML

    @app.get("/favicon.ico", status_code=204)
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/app", response_class=HTMLResponse)
    def app_page(request: Request, db: Session = Depends(get_db)):
        raw = request.cookies.get(settings.session_cookie)
        if not raw:
            return RedirectResponse("/")
        try:
            get_current_session(request, db, settings)
        except HTTPException:
            return RedirectResponse("/")
        return HTMLResponse(APP_HTML)

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(user: User = Depends(require_admin)) -> str:
        return APP_HTML

    @app.post("/api/signup", status_code=201)
    def signup(payload: AuthIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
        check_signup_rate_limit(client_ip(request, settings))
        username = normalize_username(payload.username)
        normalized = username_key(payload.username)
        password = validate_password(payload.password)
        if db.scalar(select(User).where(User.username_normalized == normalized)):
            raise HTTPException(status_code=409, detail="이미 사용 중인 사용자 이름입니다.")
        user = User(
            username=username,
            username_normalized=normalized,
            password_hash=hash_password(password),
            is_admin=False,
        )
        db.add(user)
        db.flush()
        _, csrf = create_session(db, response, request, user, settings)
        db.commit()
        return {"user": user_payload(user), "csrf_token": csrf}

    @app.post("/api/login")
    def login(payload: AuthIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
        username = username_key(payload.username)
        key = f"{client_ip(request, settings)}:{username}"
        check_rate_limit(key)
        user = db.scalar(select(User).where(User.username_normalized == username))
        if not user or not verify_password(payload.password, user.password_hash):
            record_login_failure(key)
            raise HTTPException(status_code=401, detail="사용자 이름 또는 비밀번호가 올바르지 않습니다.")
        clear_login_failures(key)
        _, csrf = create_session(db, response, request, user, settings)
        db.commit()
        return {"user": user_payload(user), "csrf_token": csrf}

    @app.post("/api/logout", status_code=204, dependencies=[Depends(require_csrf)])
    def logout(
        response: Response,
        session: AuthSession = Depends(get_current_session),
        db: Session = Depends(get_db),
    ) -> Response:
        session.revoked_at = utcnow()
        db.commit()
        out = Response(status_code=204)
        out.delete_cookie(settings.session_cookie, path="/")
        return out

    @app.get("/api/me")
    def me(
        user: User = Depends(get_current_user), session: AuthSession = Depends(get_current_session)
    ) -> dict:
        return user_payload(user, csrf_token=session.csrf_token)

    @app.get("/api/profile")
    def profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
        solve_count = (
            db.scalar(select(func.count()).select_from(ProblemSolve).where(ProblemSolve.user_id == user.id))
            or 0
        )
        attempts = db.scalars(
            select(Attempt).where(Attempt.user_id == user.id).order_by(Attempt.created_at.desc()).limit(20)
        ).all()
        return {
            "user": user_payload(user),
            "solve_count": solve_count,
            "attempts": [
                {
                    "id": attempt.id,
                    "title": attempt.snapshot.get("title"),
                    "status": attempt.status,
                    "score": attempt.score,
                    "xp_awarded": attempt.xp_awarded,
                    "started_at": iso(attempt.started_at),
                    "deadline_at": iso(attempt.deadline_at),
                    "submitted_at": iso(attempt.submitted_at),
                }
                for attempt in attempts
            ],
        }

    @app.get("/api/problems")
    def problems(
        grade: str | None = None,
        semester: str | None = None,
        unit: str | None = None,
        level: int | None = None,
        status: str | None = None,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> dict:
        stmt = (
            select(Problem, ProblemVersion)
            .join(ProblemVersion, Problem.current_version_id == ProblemVersion.id)
            .where(Problem.state == "published")
        )
        if grade:
            stmt = stmt.where(Problem.grade == grade)
        if semester:
            stmt = stmt.where(Problem.semester == semester)
        if unit:
            stmt = stmt.where(Problem.unit == unit)
        if level:
            stmt = stmt.where(Problem.level == level)
        rows = db.execute(stmt.order_by(Problem.level, Problem.external_key)).all()
        problem_ids = [problem.id for problem, _version in rows]
        solved_ids = set()
        if problem_ids:
            solved_ids = set(
                db.scalars(
                    select(ProblemSolve.problem_id).where(
                        ProblemSolve.user_id == user.id,
                        ProblemSolve.problem_id.in_(problem_ids),
                    )
                ).all()
            )
        data = []
        for problem, version in rows:
            item = public_problem(version, problem, include_solution=False)
            item["solved"] = problem.id in solved_ids
            data.append(item)
        if status == "solved":
            data = [p for p in data if p["solved"]]
        elif status == "unsolved":
            data = [p for p in data if not p["solved"]]
        elif status:
            raise HTTPException(status_code=422, detail="status는 solved 또는 unsolved만 가능합니다.")
        return {"problems": data}

    @app.get("/api/problems/{problem_id}")
    def problem_detail(
        problem_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
    ) -> dict:
        problem = db.get(Problem, problem_id)
        if not problem or problem.state != "published":
            raise HTTPException(status_code=404, detail="문제를 찾을 수 없습니다.")
        version = db.get(ProblemVersion, problem.current_version_id)
        return public_problem(version, problem, include_solution=False)

    @app.post("/api/problems/{problem_id}/submit", dependencies=[Depends(require_csrf)])
    def practice_submit(
        problem_id: str,
        payload: PracticeSubmitIn,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> dict:
        problem = db.get(Problem, problem_id)
        if not problem or problem.state != "published":
            raise HTTPException(status_code=404, detail="문제를 찾을 수 없습니다.")
        version = db.get(ProblemVersion, problem.current_version_id)
        correct = is_correct(version, payload.answer)
        xp = award_first_solve_xp(db, user, version, problem.id) if correct else 0
        db.commit()
        return {
            "correct": correct,
            "xp_awarded": xp,
            "answer_spec": version.answer_spec,
            "explanation_html": version.explanation_html,
        }

    @app.get("/api/exams")
    def exams(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
        rows = db.scalars(select(Exam).where(Exam.state == "published").order_by(Exam.slug)).all()
        return {
            "exams": [
                {
                    "id": exam.id,
                    "slug": exam.slug,
                    "title": exam.title,
                    "time_limit_seconds": exam.time_limit_seconds,
                    "current_version_id": exam.current_version_id,
                }
                for exam in rows
            ]
        }

    @app.post("/api/exams/{slug}/attempts", status_code=201, dependencies=[Depends(require_csrf)])
    def start_attempt(
        slug: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
    ) -> dict:
        exam = db.scalar(select(Exam).where(Exam.slug == slug, Exam.state == "published"))
        if not exam:
            raise HTTPException(status_code=404, detail="모의고사를 찾을 수 없습니다.")
        version = db.get(ExamVersion, exam.current_version_id)
        started_at = utcnow()
        attempt = Attempt(
            user_id=user.id,
            exam_version_id=version.id,
            snapshot=build_attempt_snapshot(db, version),
            started_at=started_at,
            deadline_at=started_at + timedelta(seconds=version.time_limit_seconds),
        )
        db.add(attempt)
        db.commit()
        return {
            "attempt_id": attempt.id,
            "started_at": iso(attempt.started_at),
            "deadline_at": iso(attempt.deadline_at),
        }

    @app.get("/api/attempts/{attempt_id}")
    def get_attempt(
        attempt_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
    ) -> dict:
        attempt = db.get(Attempt, attempt_id)
        if not attempt or attempt.user_id != user.id:
            raise HTTPException(status_code=404, detail="응시 기록을 찾을 수 없습니다.")
        return {
            "id": attempt.id,
            "status": attempt.status,
            "score": attempt.score,
            "xp_awarded": attempt.xp_awarded,
            "answers": attempt.answers,
            "flags": attempt.flags,
            "started_at": iso(attempt.started_at),
            "deadline_at": iso(attempt.deadline_at),
            "submitted_at": iso(attempt.submitted_at),
            **attempt.snapshot,
        }

    @app.patch("/api/attempts/{attempt_id}/answers", dependencies=[Depends(require_csrf)])
    def save_attempt(
        attempt_id: str,
        payload: AttemptSaveIn,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> dict:
        attempt = db.get(Attempt, attempt_id)
        if not attempt or attempt.user_id != user.id:
            raise HTTPException(status_code=404, detail="응시 기록을 찾을 수 없습니다.")
        ensure_attempt_open(attempt)
        payload_answers = {str(k): v for k, v in payload.answers.items()}
        payload_flags = {str(k): bool(v) for k, v in payload.flags.items()}
        validate_attempt_mutation(attempt, payload_answers, payload_flags)
        attempt.answers = {**(attempt.answers or {}), **payload_answers}
        attempt.flags = {**(attempt.flags or {}), **payload_flags}
        for seq, answer in payload_answers.items():
            row = db.scalar(
                select(AttemptAnswer).where(
                    AttemptAnswer.attempt_id == attempt.id, AttemptAnswer.sequence == int(seq)
                )
            )
            if not row:
                row = AttemptAnswer(attempt_id=attempt.id, sequence=int(seq))
                db.add(row)
            row.answer = answer
            row.flagged = bool(attempt.flags.get(seq, False))
        db.commit()
        return {"ok": True}

    @app.post("/api/attempts/{attempt_id}/submit", dependencies=[Depends(require_csrf)])
    def submit_attempt(
        attempt_id: str,
        payload: AttemptSubmitIn,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> dict:
        attempt = db.get(Attempt, attempt_id)
        if not attempt or attempt.user_id != user.id:
            raise HTTPException(status_code=404, detail="응시 기록을 찾을 수 없습니다.")
        answers = {str(k): v for k, v in payload.answers.items()}
        validate_attempt_mutation(attempt, answers)
        if attempt.status == "submitted":
            if attempt.submission_idempotency_key != payload.idempotency_key:
                raise HTTPException(status_code=409, detail="이미 다른 제출 키로 제출된 응시입니다.")
            if canonical_json(attempt.submitted_answers or {}) != canonical_json(answers):
                raise HTTPException(status_code=409, detail="제출 후 답안을 변경할 수 없습니다.")
            return attempt.result_snapshot
        ensure_attempt_open(attempt)
        score, xp, review = grade_attempt(db, user, attempt, answers)
        attempt.answers = answers
        attempt.submitted_answers = answers
        attempt.score = score
        attempt.xp_awarded = xp
        attempt.status = "submitted"
        attempt.submission_idempotency_key = payload.idempotency_key
        attempt.submitted_at = utcnow()
        attempt.result_snapshot = {"score": score, "xp_awarded": xp, "review": review}
        db.commit()
        return attempt.result_snapshot

    @app.get("/api/history")
    def history(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
        rows = db.scalars(
            select(Attempt).where(Attempt.user_id == user.id).order_by(Attempt.created_at.desc())
        ).all()
        return {
            "attempts": [
                {"id": a.id, "title": a.snapshot.get("title"), "status": a.status, "score": a.score}
                for a in rows
            ]
        }

    @app.get("/api/leaderboard")
    def leaderboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
        rows = db.scalars(select(User).order_by(User.total_xp.desc(), User.created_at.asc()).limit(50)).all()
        return {"users": [user_payload(row) for row in rows]}

    def admin_problem_payload(problem: Problem) -> dict:
        return {
            "id": problem.id,
            "external_key": problem.external_key,
            "title": problem.title,
            "grade": problem.grade,
            "semester": problem.semester,
            "unit": problem.unit,
            "tags": (problem.tags or {}).get("items", []),
            "level": problem.level,
            "base_xp": problem.base_xp,
            "state": problem.state,
            "current_version_id": problem.current_version_id,
            "versions": [
                {
                    "id": version.id,
                    "version_number": version.version_number,
                    "title": version.title,
                    "answer_type": version.answer_type,
                    "created_at": iso(version.created_at),
                }
                for version in sorted(problem.versions, key=lambda item: item.version_number)
            ],
        }

    def admin_exam_payload(db: Session, exam: Exam) -> dict:
        versions = db.scalars(
            select(ExamVersion).where(ExamVersion.exam_id == exam.id).order_by(ExamVersion.version_number)
        ).all()
        return {
            "id": exam.id,
            "slug": exam.slug,
            "title": exam.title,
            "time_limit_seconds": exam.time_limit_seconds,
            "state": exam.state,
            "current_version_id": exam.current_version_id,
            "versions": [
                {
                    "id": version.id,
                    "version_number": version.version_number,
                    "title": version.title,
                    "time_limit_seconds": version.time_limit_seconds,
                    "created_at": iso(version.created_at),
                }
                for version in versions
            ],
        }

    @app.get("/api/admin/problems")
    def admin_list_problems(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
        rows = db.scalars(select(Problem).order_by(Problem.external_key)).all()
        return {"problems": [admin_problem_payload(row) for row in rows]}

    @app.post("/api/admin/problems", status_code=201, dependencies=[Depends(require_csrf)])
    def admin_create_problem(
        payload: ProblemCreateIn,
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ) -> dict:
        validate_problem_meta(payload.level, payload.base_xp)
        if payload.state not in {"draft", "published"}:
            raise HTTPException(status_code=422, detail="문제 상태가 올바르지 않습니다.")
        if db.scalar(select(Problem).where(Problem.external_key == payload.external_key)):
            raise HTTPException(status_code=409, detail="이미 존재하는 문제 키입니다.")
        problem = Problem(
            external_key=payload.external_key,
            title=payload.title,
            grade=payload.grade,
            semester=payload.semester,
            unit=payload.unit,
            tags={"items": payload.tags},
            level=payload.level,
            base_xp=payload.base_xp,
            state=payload.state,
        )
        db.add(problem)
        db.flush()
        version_payload = problem_version_payload(payload)
        version = ProblemVersion(
            problem_id=problem.id,
            version_number=1,
            content_hash=digest(version_payload),
            **version_payload,
        )
        db.add(version)
        db.flush()
        problem.current_version_id = version.id if payload.state == "published" else version.id
        db.commit()
        return {"problem": admin_problem_payload(problem)}

    @app.post(
        "/api/admin/problems/{problem_id}/versions", status_code=201, dependencies=[Depends(require_csrf)]
    )
    def admin_create_problem_version(
        problem_id: str,
        payload: ProblemVersionIn,
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ) -> dict:
        problem = db.get(Problem, problem_id)
        if not problem:
            raise HTTPException(status_code=404, detail="문제를 찾을 수 없습니다.")
        version_payload = problem_version_payload(payload)
        content_hash = digest(version_payload)
        if db.scalar(
            select(ProblemVersion).where(
                ProblemVersion.problem_id == problem.id, ProblemVersion.content_hash == content_hash
            )
        ):
            raise HTTPException(status_code=409, detail="같은 내용의 문제 버전이 이미 있습니다.")
        next_number = (
            db.scalar(
                select(func.max(ProblemVersion.version_number)).where(ProblemVersion.problem_id == problem.id)
            )
            or 0
        ) + 1
        version = ProblemVersion(
            problem_id=problem.id,
            version_number=next_number,
            content_hash=content_hash,
            **version_payload,
        )
        db.add(version)
        db.commit()
        return {
            "problem_version": {
                "id": version.id,
                "problem_id": problem.id,
                "version_number": version.version_number,
                "title": version.title,
            }
        }

    @app.post("/api/admin/problems/{problem_id}/publish", dependencies=[Depends(require_csrf)])
    def admin_publish_problem(
        problem_id: str,
        payload: PublishIn,
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ) -> dict:
        problem = db.get(Problem, problem_id)
        version = db.get(ProblemVersion, payload.version_id)
        if not problem or not version or version.problem_id != problem.id:
            raise HTTPException(status_code=422, detail="문제 버전 참조가 올바르지 않습니다.")
        problem.current_version_id = version.id
        problem.title = version.title
        problem.state = "published"
        db.commit()
        return {"problem": admin_problem_payload(problem)}

    @app.get("/api/admin/exams")
    def admin_list_exams(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
        rows = db.scalars(select(Exam).order_by(Exam.slug)).all()
        return {"exams": [admin_exam_payload(db, row) for row in rows]}

    @app.post("/api/admin/exams", status_code=201, dependencies=[Depends(require_csrf)])
    def admin_create_exam(
        payload: ExamCreateIn,
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ) -> dict:
        if payload.time_limit_seconds <= 0:
            raise HTTPException(status_code=422, detail="시험 제한 시간은 1초 이상이어야 합니다.")
        if payload.state not in {"draft", "published"}:
            raise HTTPException(status_code=422, detail="시험 상태가 올바르지 않습니다.")
        if db.scalar(select(Exam).where(Exam.slug == payload.slug)):
            raise HTTPException(status_code=409, detail="이미 존재하는 시험 slug입니다.")
        exam = Exam(
            slug=payload.slug,
            title=payload.title,
            time_limit_seconds=payload.time_limit_seconds,
            state=payload.state,
        )
        db.add(exam)
        db.commit()
        return {"exam": admin_exam_payload(db, exam)}

    @app.post("/api/admin/exams/{exam_id}/versions", status_code=201, dependencies=[Depends(require_csrf)])
    def admin_create_exam_version(
        exam_id: str,
        payload: ExamVersionIn,
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ) -> dict:
        exam = db.get(Exam, exam_id)
        if not exam:
            raise HTTPException(status_code=404, detail="시험을 찾을 수 없습니다.")
        if payload.time_limit_seconds <= 0:
            raise HTTPException(status_code=422, detail="시험 제한 시간은 1초 이상이어야 합니다.")
        sequences = [item.sequence for item in payload.items]
        if len(sequences) != len(set(sequences)):
            raise HTTPException(status_code=422, detail="시험 문항 sequence가 중복되었습니다.")
        if any(item.sequence <= 0 or item.points <= 0 for item in payload.items):
            raise HTTPException(status_code=422, detail="sequence와 배점은 1 이상이어야 합니다.")
        version_ids = [item.problem_version_id for item in payload.items]
        versions = {
            version.id: version
            for version in db.scalars(select(ProblemVersion).where(ProblemVersion.id.in_(version_ids))).all()
        }
        if len(versions) != len(set(version_ids)):
            raise HTTPException(status_code=422, detail="문제 버전 참조가 올바르지 않습니다.")
        content = {
            "title": payload.title,
            "time_limit_seconds": payload.time_limit_seconds,
            "items": [
                {
                    "sequence": item.sequence,
                    "problem_version_id": item.problem_version_id,
                    "points": item.points,
                }
                for item in sorted(payload.items, key=lambda row: row.sequence)
            ],
        }
        content_hash = digest(content)
        if db.scalar(
            select(ExamVersion).where(
                ExamVersion.exam_id == exam.id, ExamVersion.content_hash == content_hash
            )
        ):
            raise HTTPException(status_code=409, detail="같은 내용의 시험 버전이 이미 있습니다.")
        next_number = (
            db.scalar(select(func.max(ExamVersion.version_number)).where(ExamVersion.exam_id == exam.id)) or 0
        ) + 1
        exam_version = ExamVersion(
            exam_id=exam.id,
            version_number=next_number,
            title=payload.title,
            time_limit_seconds=payload.time_limit_seconds,
            content_hash=content_hash,
        )
        db.add(exam_version)
        db.flush()
        for item in payload.items:
            version = versions[item.problem_version_id]
            db.add(
                ExamVersionItem(
                    exam_version_id=exam_version.id,
                    problem_version_id=version.id,
                    problem_id=version.problem_id,
                    sequence=item.sequence,
                    points=item.points,
                )
            )
        db.commit()
        return {
            "exam_version": {
                "id": exam_version.id,
                "exam_id": exam.id,
                "version_number": exam_version.version_number,
                "title": exam_version.title,
            }
        }

    @app.post("/api/admin/exams/{exam_id}/publish", dependencies=[Depends(require_csrf)])
    def admin_publish_exam(
        exam_id: str,
        payload: PublishIn,
        db: Session = Depends(get_db),
        admin: User = Depends(require_admin),
    ) -> dict:
        exam = db.get(Exam, exam_id)
        version = db.get(ExamVersion, payload.version_id)
        if not exam or not version or version.exam_id != exam.id:
            raise HTTPException(status_code=422, detail="시험 버전 참조가 올바르지 않습니다.")
        exam.current_version_id = version.id
        exam.title = version.title
        exam.time_limit_seconds = version.time_limit_seconds
        exam.state = "published"
        db.commit()
        return {"exam": admin_exam_payload(db, exam)}

    @app.post("/api/admin/import", dependencies=[Depends(require_csrf)])
    def admin_import(
        payload: ImportIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)
    ) -> dict:
        return import_bundle(db, payload.path, dry_run=payload.dry_run)

    @app.get("/static/app.css")
    def css() -> Response:
        return Response(APP_CSS, media_type="text/css")

    @app.get("/static/app.js")
    def js() -> Response:
        return Response(APP_JS, media_type="application/javascript")

    def pdf_response(path: str, include_body: bool = True) -> Response:
        file_path = Path(path)
        data = file_path.read_bytes() if include_body else b""
        headers = {"content-length": str(file_path.stat().st_size)}
        return Response(data, media_type="application/pdf", headers=headers)

    @app.get("/middle-math-70-exam.pdf")
    def exam_pdf() -> Response:
        return pdf_response("middle-math-70-exam.pdf")

    @app.head("/middle-math-70-exam.pdf")
    def exam_pdf_head() -> Response:
        return pdf_response("middle-math-70-exam.pdf", include_body=False)

    @app.get("/middle-math-70-solutions.pdf")
    def sol_pdf() -> Response:
        return pdf_response("middle-math-70-solutions.pdf")

    @app.head("/middle-math-70-solutions.pdf")
    def sol_pdf_head() -> Response:
        return pdf_response("middle-math-70-solutions.pdf", include_body=False)

    return app


APP_CSS = """
:root{--bg:#eef1ed;--panel:#fff;--ink:#17211f;--muted:#66716e;--line:#cbd2ce;--accent:#315e58;--accent2:#e2eeea;--danger:#963f37;--focus:#d58a36}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;max-width:100%;overflow-x:hidden;font-family:"Noto Sans KR","Apple SD Gothic Neo",system-ui,sans-serif;color:var(--ink);background:var(--bg);word-break:keep-all}button,input,select{font:inherit}button,a{touch-action:manipulation}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--focus);outline-offset:2px}.auth-page{min-height:100dvh;display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:40px;align-items:center;max-width:1120px;margin:auto;padding:32px}.hero h1{font-size:clamp(40px,8vw,76px);line-height:1;letter-spacing:0;margin:0 0 18px}.eyebrow{font-size:12px;font-weight:900;color:var(--accent);letter-spacing:.12em}.auth-panel,.panel,.problem-card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:22px;box-shadow:0 16px 42px rgba(25,48,45,.08)}label{display:grid;gap:6px;margin:12px 0;color:var(--muted);font-size:14px}input,select{width:100%;border:1px solid var(--line);border-radius:7px;padding:12px;background:#fff;color:var(--ink)}button{border:1px solid var(--line);border-radius:7px;background:#fff;color:var(--ink);font-weight:800;padding:10px 14px;cursor:pointer;min-width:0}button.primary,button[data-login],button[data-signup]{background:var(--accent);border-color:var(--accent);color:#fff}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}.topbar{position:sticky;top:0;z-index:10;min-height:62px;background:#f8faf7;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;padding:0 18px}.brand{font-size:18px;font-weight:900;color:var(--ink);text-decoration:none;white-space:nowrap}.topbar nav{display:flex;gap:4px;flex:1;min-width:0}.topbar nav button{padding:8px 10px;background:transparent;white-space:nowrap}.shell main{max-width:1180px;margin:auto;padding:22px 16px 80px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;align-items:end;margin-bottom:12px}.problem-card{text-align:left;display:grid;gap:8px}.badge{width:44px;height:44px}.muted{color:var(--muted)}.workstation{display:grid;grid-template-columns:260px minmax(0,1fr);gap:14px}.palette summary{display:none}.list{display:grid;gap:8px}.question{background:#fff;border:1px solid var(--line);padding:22px;border-radius:8px;min-height:320px;min-width:0}.question svg{max-width:100%;height:auto}.choices{display:grid;gap:8px}.choice{width:100%;text-align:left}.choice.selected,.flagged{background:var(--accent2);border-color:var(--accent)}.status{min-height:24px;color:var(--accent);font-weight:800}.error{color:var(--danger)}table{width:100%;border-collapse:collapse;background:#fff}td,th{border-bottom:1px solid var(--line);padding:10px;text-align:left}@media(max-width:720px){.auth-page{grid-template-columns:1fr;padding:18px}.topbar{height:auto;align-items:flex-start;flex-wrap:wrap;padding:10px}.topbar nav{order:3;flex-basis:100%;overflow-x:auto}.shell main{padding:14px 10px 60px}.workstation{grid-template-columns:1fr}.palette summary{display:block;margin:8px 0 12px;padding:12px;border:1px solid var(--line);border-radius:6px;background:#fff;font-weight:800;cursor:pointer}.hero h1{font-size:42px}}@media(max-width:390px){button{padding:9px 10px}.auth-panel,.panel,.problem-card,.question{padding:16px}.topbar{gap:6px}.topbar nav button{padding:8px}}
"""


APP_JS = """
let csrfToken='', me=null, currentAttempt=null, currentSeq=1, pendingAnswers={}, pendingFlags={}, saveTimer=null, timerHandle=null;
const $=s=>document.querySelector(s); const root=()=>$('#appRoot');
const h=v=>String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));
const status=(m,bad=false)=>{const e=$('#authStatus')||$('#status'); if(e){e.textContent=m; e.className=bad?'error status':'status'}};
async function api(path, opts={}){opts.headers={...(opts.headers||{}),'Content-Type':'application/json'}; if(csrfToken && !['GET','HEAD'].includes(opts.method||'GET')) opts.headers['X-CSRF-Token']=csrfToken; const r=await fetch(path,opts); if(r.status===401){location.href='/'; return null} if(!r.ok){let j; try{j=await r.json()}catch{j={detail:'요청 실패'}} throw new Error(j.detail||'요청 실패')} if(r.status===204)return {}; return r.json()}
async function bootAuth(){const f=$('#authForm'); if(!f)return; f.addEventListener('submit',e=>{e.preventDefault(); submitAuth('/api/login')}); $('[data-signup]').addEventListener('click',()=>submitAuth('/api/signup'))}
async function submitAuth(path){try{const f=$('#authForm'); const r=await api(path,{method:'POST',body:JSON.stringify({username:f.username.value,password:f.password.value})}); csrfToken=r.csrf_token; location.href='/app'}catch(e){status(e.message,true)}}
async function bootApp(){if(!root())return; try{const r=await api('/api/me'); me=r; csrfToken=r.csrf_token; document.querySelectorAll('[data-admin-only]').forEach(x=>x.hidden=!me.is_admin); document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>render(b.dataset.view)); $('#logoutBtn').onclick=logout; const id=location.hash.startsWith('#attempt-')?location.hash.slice(9):''; if(id){await loadAttempt(id); return} await render('dashboard')}catch(e){root().innerHTML='<section class=\"panel\"><h1>로그인</h1><p>로그인이 필요합니다.</p><a href=\"/\">로그인 화면으로</a></section>'}}
async function logout(){await api('/api/logout',{method:'POST'}); location.href='/'}
async function render(view){clearInterval(timerHandle); if(view==='dashboard')return dashboard(); if(view==='problems')return problemList(); if(view==='exams')return examList(); if(view==='profile')return profileView(); if(view==='leaderboard')return leaderboard(); if(view==='admin')return adminView()}
async function dashboard(){const p=await api('/api/profile'); root().innerHTML=`<h1>대시보드</h1><section class=\"grid\"><article class=\"panel\"><h2>${h(p.user.username)}</h2><p>${p.user.total_xp} XP · ${h(p.user.tier.label_ko)} · 풀이 ${p.solve_count}</p></article><article class=\"panel\"><h2>최근 응시</h2>${p.attempts.length?`<p>${h(p.attempts[0].title)} · ${p.attempts[0].score}점</p><button data-resume=\"${h(p.attempts[0].id)}\">이어보기</button>`:'<p>아직 기록 없음</p>'}</article></section><p id=\"status\" class=\"status\"></p>`; document.querySelectorAll('[data-resume]').forEach(b=>b.onclick=()=>loadAttempt(b.dataset.resume))}
function problemQuery(){const p=new URLSearchParams(); ['grade','semester','unit','level','status'].forEach(k=>{const v=$('#f_'+k)?.value; if(v)p.set(k,v)}); return p.toString()?'/api/problems?'+p.toString():'/api/problems'}
async function problemList(){const r=await api(problemQuery()); const probs=r.problems; const opts=(key)=>[...new Set(probs.map(p=>p[key]).filter(Boolean))].map(v=>`<option>${h(v)}</option>`).join(''); root().innerHTML=`<h1>문제</h1><section class=\"panel filters\"><label>학년<select id=\"f_grade\"><option value=\"\">전체</option>${opts('grade')}</select></label><label>학기<select id=\"f_semester\"><option value=\"\">전체</option>${opts('semester')}</select></label><label>단원<input id=\"f_unit\" placeholder=\"단원\"></label><label>레벨<input id=\"f_level\" type=\"number\" min=\"1\" max=\"30\"></label><label>상태<select id=\"f_status\"><option value=\"\">전체</option><option value=\"solved\">해결</option><option value=\"unsolved\">미해결</option></select></label><button id=\"applyFilters\">적용</button></section><div class=\"grid\">${probs.map(p=>`<button class=\"problem-card\" data-p=\"${h(p.id)}\"><span class=\"badge\">${p.tier_badge_svg}</span><strong>${h(p.title)}</strong><span class=\"muted\">${h(p.unit)} · ${h(p.tier.label_ko)} · ${p.base_xp} XP · ${p.solved?'해결':'미해결'}</span></button>`).join('')}</div><p id=\"status\" class=\"status\"></p>`; $('#applyFilters').onclick=problemList; document.querySelectorAll('[data-p]').forEach(b=>b.onclick=()=>problemDetail(b.dataset.p))}
async function problemDetail(id){const p=await api('/api/problems/'+id); root().innerHTML=`<button data-view=\"problems\">← 목록</button><article class=\"question\"><p class=\"muted\">${h(p.tier.label_ko)}</p><h1>${h(p.title)}</h1>${p.diagram_svg}<div>${p.body_html}</div><div class=\"choices\">${p.choices.map(c=>`<button class=\"choice\" data-choice=\"${c.index}\">${c.index+1}. ${h(c.text)}</button>`).join('')}</div><label>답 입력<input id=\"textAnswer\"></label><button class=\"primary\" id=\"submitPractice\">제출</button><div id=\"result\"></div></article>`; $('[data-view]').onclick=()=>problemList(); document.querySelectorAll('[data-choice]').forEach(b=>b.onclick=()=>{document.querySelectorAll('.choice').forEach(x=>x.classList.remove('selected')); b.classList.add('selected')}); $('#submitPractice').onclick=async()=>{const picked=$('.choice.selected'); const answer=p.answer_type==='choice'?{choice:Number(picked?.dataset.choice??-1)}:{text:$('#textAnswer').value}; const r=await api('/api/problems/'+id+'/submit',{method:'POST',body:JSON.stringify({answer,idempotency_key:crypto.randomUUID()})}); $('#result').innerHTML=`<h2>${r.correct?'정답':'오답'}</h2><p>${r.xp_awarded} XP</p>${r.explanation_html}`; me=await api('/api/me')}}
async function examList(){const r=await api('/api/exams'); root().innerHTML=`<h1>모의고사</h1><div class=\"list\">${r.exams.map(e=>`<section class=\"panel\"><h2>${h(e.title)}</h2><p>${Math.round(e.time_limit_seconds/60)}분</p><button class=\"primary\" data-exam=\"${h(e.slug)}\">시작</button></section>`).join('')}</div>`; document.querySelectorAll('[data-exam]').forEach(b=>b.onclick=()=>startExam(b.dataset.exam))}
async function startExam(slug){const r=await api('/api/exams/'+slug+'/attempts',{method:'POST',body:'{}'}); location.hash='attempt-'+r.attempt_id; await loadAttempt(r.attempt_id)}
async function loadAttempt(id){currentAttempt=await api('/api/attempts/'+id); pendingAnswers={...(currentAttempt.answers||{})}; pendingFlags={...(currentAttempt.flags||{})}; currentSeq=Number(currentAttempt.items?.[0]?.sequence||1); renderAttempt()}
function answerFor(item){return pendingAnswers[String(item.sequence)]||{}}
function writeCurrent(item){const picked=$('.choice.selected'); if(item.answer_type==='choice'){if(picked)pendingAnswers[String(item.sequence)]={choice:Number(picked.dataset.choice)}}else{pendingAnswers[String(item.sequence)]={text:$('#examText')?.value||''}}}
function renderAttempt(){clearInterval(timerHandle); const item=currentAttempt.items.find(x=>x.sequence===currentSeq); const ans=answerFor(item); root().innerHTML=`<h1>${h(currentAttempt.title)}</h1><div class=\"workstation\"><aside class=\"panel\"><p id=\"timer\" class=\"status\"></p><details class=\"palette\" ${window.matchMedia('(min-width:721px)').matches?'open':''}><summary>문항표 열기</summary><div class=\"grid\">${currentAttempt.items.map(i=>`<button data-jump=\"${i.sequence}\" class=\"${pendingFlags[String(i.sequence)]?'flagged':''}\">${i.sequence}${pendingAnswers[String(i.sequence)]?' ✓':''}</button>`).join('')}</div></details><button id=\"submitExam\" class=\"primary\">제출 검토</button></aside><article class=\"question\"><p>${currentSeq} / ${currentAttempt.items.length}</p><h2>${h(item.title)}</h2>${item.diagram_svg}<div>${item.body_html}</div><div class=\"choices\">${item.choices.map(c=>`<button class=\"choice ${ans.choice===c.index?'selected':''}\" data-choice=\"${c.index}\">${c.index+1}. ${h(c.text)}</button>`).join('')}</div><label>답<input id=\"examText\" value=\"${h(ans.text||'')}\"></label><label class=\"flag\"><input id=\"flagBox\" type=\"checkbox\" ${pendingFlags[String(item.sequence)]?'checked':''}> 검토 표시</label><div class=\"actions\"><button id=\"prev\">이전</button><button id=\"save\">저장</button><button id=\"next\">다음</button></div><p id=\"status\" class=\"status\"></p></article></div><section id=\"submitReview\" class=\"panel\" hidden></section>`; document.querySelectorAll('[data-jump]').forEach(b=>b.onclick=()=>{writeCurrent(item); currentSeq=Number(b.dataset.jump); renderAttempt()}); document.querySelectorAll('[data-choice]').forEach(b=>b.onclick=()=>{document.querySelectorAll('.choice').forEach(x=>x.classList.remove('selected')); b.classList.add('selected'); writeCurrent(item); debounceSave(item)}); $('#examText').oninput=()=>{writeCurrent(item); debounceSave(item)}; $('#flagBox').onchange=()=>{pendingFlags[String(item.sequence)]=$('#flagBox').checked; debounceSave(item); renderAttempt()}; $('#prev').onclick=()=>{writeCurrent(item); currentSeq=Math.max(1,currentSeq-1); renderAttempt()}; $('#next').onclick=()=>{writeCurrent(item); currentSeq=Math.min(currentAttempt.items.length,currentSeq+1); renderAttempt()}; $('#save').onclick=()=>saveServer(item); $('#submitExam').onclick=()=>openSubmitReview(item); startTimer(); root().focus()}
function debounceSave(item){status('저장 중...'); clearTimeout(saveTimer); saveTimer=setTimeout(()=>saveServer(item),350)}
async function saveServer(item){try{writeCurrent(item); await api('/api/attempts/'+currentAttempt.id+'/answers',{method:'PATCH',body:JSON.stringify({answers:{[item.sequence]:pendingAnswers[String(item.sequence)]||{}},flags:{[item.sequence]:!!pendingFlags[String(item.sequence)]}})}); status('저장됨')}catch(e){status(e.message,true)}}
function openSubmitReview(item){writeCurrent(item); const unanswered=currentAttempt.items.filter(i=>!pendingAnswers[String(i.sequence)]); const flagged=currentAttempt.items.filter(i=>pendingFlags[String(i.sequence)]); const box=$('#submitReview'); box.hidden=false; box.innerHTML=`<h2>제출 검토</h2><p>미응답 ${unanswered.length}개 · 검토 ${flagged.length}개</p><p class=\"muted\">미응답: ${h(unanswered.map(i=>i.sequence).join(', ')||'없음')}</p><p class=\"muted\">검토: ${h(flagged.map(i=>i.sequence).join(', ')||'없음')}</p><div class=\"actions\"><button data-return>돌아가기</button><button class=\"primary\" data-final-submit>최종 제출</button></div>`; $('[data-return]').onclick=()=>box.hidden=true; $('[data-final-submit]').onclick=submitExam}
async function submitExam(){try{const r=await api('/api/attempts/'+currentAttempt.id+'/submit',{method:'POST',body:JSON.stringify({answers:pendingAnswers,idempotency_key:crypto.randomUUID()})}); clearInterval(timerHandle); location.hash=''; root().innerHTML=`<section class=\"panel\"><h1>결과</h1><p>${r.score}점 · ${r.xp_awarded} XP</p><div>${r.review.map(x=>`<details><summary>${x.sequence}번 ${x.points}점 ${x.correct?'정답':'오답'}</summary>${x.explanation_html}</details>`).join('')}</div></section>`}catch(e){status(e.message,true)}}
function startTimer(){const tick=()=>{const e=$('#timer'); if(!e)return; const left=Math.max(0,Math.floor((new Date(currentAttempt.deadline_at)-new Date())/1000)); e.textContent=`남은 시간 ${Math.floor(left/60)}:${String(left%60).padStart(2,'0')}`; if(left<=0){status('응시 시간이 종료되었습니다.',true); clearInterval(timerHandle)}}; tick(); timerHandle=setInterval(tick,1000)}
async function profileView(){const p=await api('/api/profile'); root().innerHTML=`<h1>프로필</h1><section class=\"grid\"><article class=\"panel\"><h2>${h(p.user.username)}</h2><p>${p.user.total_xp} XP · ${h(p.user.tier.label_ko)} · 풀이 ${p.solve_count}</p></article><article class=\"panel\"><h2>응시 기록</h2><div class=\"list\">${p.attempts.map(a=>`<button data-resume=\"${h(a.id)}\">${h(a.title)} · ${h(a.status)} · ${a.score}점</button>`).join('')||'<p>기록 없음</p>'}</div></article></section>`; document.querySelectorAll('[data-resume]').forEach(b=>b.onclick=()=>loadAttempt(b.dataset.resume))}
async function leaderboard(){const r=await api('/api/leaderboard'); root().innerHTML=`<h1>순위</h1><table><thead><tr><th>순위</th><th>사용자</th><th>XP</th><th>티어</th></tr></thead><tbody>${r.users.map((u,i)=>`<tr><td>${i+1}</td><td>${h(u.username)}</td><td>${u.total_xp}</td><td>${h(u.tier.label_ko)}</td></tr>`).join('')}</tbody></table>`}
async function adminView(){try{const [p,e]=await Promise.all([api('/api/admin/problems'),api('/api/admin/exams')]); root().innerHTML=`<h1>관리</h1><section class=\"panel\"><h2>문제 관리</h2><p>${p.problems.length}개 문제 · 초안 포함</p><div class=\"list\">${p.problems.slice(0,20).map(x=>`<span>${h(x.external_key)} · ${h(x.title)} · ${h(x.state)}</span>`).join('')}</div></section><section class=\"panel\"><h2>시험 관리</h2><p>${e.exams.length}개 시험</p></section><section class=\"panel\"><h2>번들 가져오기</h2><label>경로<input id=\"bundlePath\" value=\"content/bundles/math70-v2.json\"></label><div class=\"actions\"><button id=\"dry\">검증</button><button id=\"import\" class=\"primary\">가져오기</button></div><pre id=\"adminOut\"></pre></section>`; $('#dry').onclick=()=>runImport(true); $('#import').onclick=()=>runImport(false)}catch(err){root().innerHTML=`<section class=\"panel\"><h1>관리</h1><p class=\"error\">${h(err.message)}</p></section>`}}
async function runImport(dry_run){const r=await api('/api/admin/import',{method:'POST',body:JSON.stringify({path:$('#bundlePath').value,dry_run})}); $('#adminOut').textContent=JSON.stringify(r,null,2)}
document.addEventListener('keydown',e=>{if(!currentAttempt||!currentAttempt.items)return; const item=currentAttempt.items.find(x=>x.sequence===currentSeq); if(e.key==='ArrowRight'){$('#next')?.click()} if(e.key==='ArrowLeft'){$('#prev')?.click()} if(e.key.toLowerCase()==='f'){const f=$('#flagBox'); if(f){f.checked=!f.checked; f.dispatchEvent(new Event('change'))}}});
bootAuth(); bootApp();
"""
