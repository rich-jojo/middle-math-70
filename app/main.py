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
    if not value:
        return None
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return normalized.isoformat()


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
<meta name="theme-color" content="#f3f6f2"><meta name="description" content="중1-2, 중2-1, 중2-2 수학 모의고사와 문제 풀이 기록을 한곳에서 관리합니다.">
<title>수학 70 | 중등 수학 집중 훈련</title><link rel="stylesheet" href="/static/app.css"></head>
<body class="auth-body"><a class="skip-link" href="#main">본문 바로가기</a>
<main id="main" class="auth-page"><section class="hero" aria-labelledby="hero-title">
<p class="subject-line">중등 수학 집중 훈련</p><h1 id="hero-title">시험장에서<br><span>70점</span>을 넘는 연습</h1>
<p class="hero-copy">중1-2, 중2-1, 중2-2 범위를 실전 순서로 풀고 답안과 응시 기록은 바로 저장됩니다.</p>
<div class="exam-brief" aria-label="시험 구성"><div><strong>25</strong><span>문항</span></div><div><strong>100</strong><span>점</span></div><div><strong>120</strong><span>분</span></div></div>
</section><section class="auth-panel" aria-label="로그인과 가입"><div class="panel-heading"><p>학습 기록 시작</p><h2>계정으로 들어가기</h2></div>
<form id="authForm"><label>사용자 이름<input name="username" autocomplete="username" required maxlength="64" placeholder="이름을 입력하세요"></label>
<label>비밀번호<input name="password" type="password" autocomplete="current-password" required maxlength="256" placeholder="비밀번호를 입력하세요"></label>
<div class="auth-actions"><button type="submit" data-login>로그인</button><button type="button" class="secondary" data-signup>새 계정 만들기</button></div>
<p class="form-note">답안, 점수, 오답 기록을 서버에 보관합니다.</p><p id="authStatus" class="status" role="status" aria-live="polite"></p></form>
</section></main><script src="/static/app.js"></script></body></html>"""


APP_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#f3f6f2"><title>수학 70 | 학습실</title><link rel="stylesheet" href="/static/app.css"></head>
<body><a class="skip-link" href="#appRoot">본문 바로가기</a><div class="shell"><header class="topbar">
<a class="brand" href="/app" aria-label="수학 70 홈"><span class="brand-mark">70</span><span>수학 훈련실</span></a>
<nav aria-label="주요 메뉴"><button data-view="dashboard">홈</button><button data-view="problems">문제은행</button><button data-view="exams">모의고사</button><button data-view="profile">기록</button><button data-view="leaderboard">순위</button><button data-view="admin" data-admin-only hidden>관리</button></nav>
<button id="logoutBtn" class="quiet">로그아웃</button></header><main id="appRoot" tabindex="-1"><div class="loading" role="status"><span></span><p>학습 기록을 불러오는 중</p></div></main></div>
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

    return app


STATIC_DIR = Path(__file__).parent / "static"
APP_CSS = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
