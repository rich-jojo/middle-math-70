from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ProblemSolve, ProblemVersion, User, XpLedger


def normalize_answer(value: Any) -> str:
    return re.sub(r"[\s,]", "", unicodedata.normalize("NFKC", str(value or ""))).replace("−", "-").lower()


NEGATION_MARKERS = ("아니", "않", "틀리", "거짓")


def contains_asserted_token(value: str, token: str) -> bool:
    """Return true only when a rubric token is not locally negated."""
    start = 0
    while (index := value.find(token, start)) != -1:
        prefix = value[max(0, index - 6) : index]
        suffix = value[index + len(token) : index + len(token) + 10]
        if not any(marker in prefix or marker in suffix for marker in NEGATION_MARKERS):
            return True
        start = index + 1
    return False


def is_correct(version: ProblemVersion, answer: dict[str, Any]) -> bool:
    spec = version.answer_spec
    if version.answer_type == "choice":
        return int(answer.get("choice", -1)) == int(spec["correct_index"])
    value = answer.get("text", answer.get("value", ""))
    return normalize_answer(value) in {normalize_answer(v) for v in spec.get("accepted", [])}


def grade_points(version: ProblemVersion, answer: dict, points: int) -> int:
    if is_correct(version, answer):
        return points
    if version.answer_type != "process":
        return 0
    value = normalize_answer(answer.get("text"))
    earned = 0
    for part in version.answer_spec.get("partial", []):
        tokens = [normalize_answer(token) for token in part.get("tokens", [])]
        if any(token and contains_asserted_token(value, token) for token in tokens):
            earned += int(part.get("points", 0))
    return min(points, earned)


def award_first_solve_xp(
    db: Session, user: User, version: ProblemVersion, problem_id: str, attempt_id: str | None = None
) -> int:
    try:
        with db.begin_nested():
            db.add(ProblemSolve(user_id=user.id, problem_id=problem_id, first_problem_version_id=version.id))
            db.flush()
    except IntegrityError:
        return 0
    amount = version.problem.base_xp
    db.add(
        XpLedger(
            user_id=user.id,
            problem_id=problem_id,
            attempt_id=attempt_id,
            amount=amount,
            reason="first_solve",
            idempotency_key=f"first_solve:{user.id}:{problem_id}",
        )
    )
    db.execute(update(User).where(User.id == user.id).values(total_xp=User.total_xp + amount))
    return amount
