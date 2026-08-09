from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import bleach
from jsonschema import Draft202012Validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Exam, ExamVersion, ExamVersionItem, Problem, ProblemVersion

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_SCHEMA = ROOT / "content/schema/problem-bundle-v1.schema.json"

ALLOWED_TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {
    "p",
    "div",
    "span",
    "ol",
    "ul",
    "li",
    "strong",
    "em",
    "br",
    "svg",
    "rect",
    "path",
    "circle",
    "ellipse",
    "polygon",
    "polyline",
    "line",
    "text",
    "g",
    "table",
    "caption",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}
ALLOWED_ATTRS = {
    "*": ["class", "aria-label", "role"],
    "svg": ["viewBox", "role", "aria-label", "xmlns"],
    "rect": ["x", "y", "width", "height", "fill", "stroke", "stroke-width"],
    "path": ["d", "stroke", "stroke-width", "fill", "stroke-dasharray"],
    "circle": ["cx", "cy", "r", "fill", "stroke", "stroke-width"],
    "ellipse": ["cx", "cy", "rx", "ry", "fill", "stroke", "stroke-width"],
    "polygon": ["points", "fill", "stroke", "stroke-width"],
    "polyline": ["points", "fill", "stroke", "stroke-width"],
    "line": ["x1", "y1", "x2", "y2", "stroke", "stroke-width", "stroke-dasharray"],
    "text": ["x", "y", "text-anchor", "font-size", "font-family", "fill"],
    "th": ["scope", "colspan", "rowspan"],
    "td": ["colspan", "rowspan"],
}


def _resolve(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def clean_html(value: str) -> str:
    return bleach.clean(value or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _normalize_contract_text(value: Any) -> str:
    return re.sub(r"[\s,]", "", unicodedata.normalize("NFKC", str(value or ""))).replace("−", "-").lower()


def load_bundle(path: str | Path) -> dict:
    bundle = json.loads(_resolve(path).read_text(encoding="utf-8"))
    schema = json.loads(BUNDLE_SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(bundle), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        loc = ".".join(str(p) for p in first.path) or "$"
        raise ValueError(f"{loc}: {first.message}")
    keys = [p["external_key"] for p in bundle["problems"]]
    if len(keys) != len(set(keys)):
        raise ValueError("problem external_key duplicate in bundle")
    for problem in bundle["problems"]:
        if problem["answer_type"] == "choice":
            idx = problem["answer_spec"].get("correct_index")
            if idx is None or idx < 0 or idx >= len(problem.get("choices", [])):
                raise ValueError(f"{problem['external_key']}: choice answer mismatch")
            choices = [_normalize_contract_text(choice) for choice in problem.get("choices", [])]
            if len(choices) != len(set(choices)):
                raise ValueError(f"{problem['external_key']}: choice options must be unique")
        if problem["answer_type"] in {"text", "process"} and not problem["answer_spec"].get("accepted"):
            raise ValueError(f"{problem['external_key']}: accepted answers required")
        if problem["answer_type"] == "process":
            tokens = [
                _normalize_contract_text(token)
                for part in problem["answer_spec"].get("partial", [])
                for token in part.get("tokens", [])
            ]
            if len(tokens) != len(set(tokens)):
                raise ValueError(f"{problem['external_key']}: rubric tokens must be unique")
    known = set(keys)
    for exam in bundle["exams"]:
        seen_seq = set()
        for item in exam["items"]:
            if item["problem_external_key"] not in known:
                raise ValueError(f"{exam['slug']}: missing problem ref {item['problem_external_key']}")
            if item["sequence"] in seen_seq:
                raise ValueError(f"{exam['slug']}: duplicate item sequence")
            seen_seq.add(item["sequence"])
        if sorted(seen_seq) != list(range(1, len(exam["items"]) + 1)):
            raise ValueError(f"{exam['slug']}: item sequences must be contiguous")
    return bundle


def import_bundle(db: Session, path: str | Path, dry_run: bool = False) -> dict:
    bundle = load_bundle(path)
    result = {
        "valid": True,
        "dry_run": dry_run,
        "created_problems": 0,
        "updated_problem_versions": 0,
        "created_exams": 0,
        "updated_exam_versions": 0,
    }
    if dry_run:
        result["problems"] = len(bundle["problems"])
        result["exams"] = len(bundle["exams"])
        return result

    for item in bundle["problems"]:
        external_key = item["external_key"]
        problem = db.scalar(select(Problem).where(Problem.external_key == external_key))
        if not problem:
            problem = Problem(
                external_key=external_key,
                title=item["title"],
                grade=item.get("grade", ""),
                semester=item.get("semester", ""),
                unit=item.get("unit", ""),
                tags={"items": item.get("tags", [])},
                level=item["level"],
                base_xp=item["base_xp"],
                state=item.get("state", "published"),
            )
            db.add(problem)
            db.flush()
            result["created_problems"] += 1
        else:
            problem.title = item["title"]
            problem.grade = item.get("grade", "")
            problem.semester = item.get("semester", "")
            problem.unit = item.get("unit", "")
            problem.tags = {"items": item.get("tags", [])}
            problem.level = item["level"]
            problem.base_xp = item["base_xp"]
            problem.state = item.get("state", "published")

        version_payload = {
            "title": item["title"],
            "body_html": clean_html(item["body_html"]),
            "answer_type": item["answer_type"],
            "choices": item.get("choices", []),
            "answer_spec": item["answer_spec"],
            "explanation_html": clean_html(item.get("explanation_html", "")),
            "diagram_svg": clean_html(item.get("diagram_svg", "")),
        }
        content_hash = digest(version_payload)
        current = db.scalar(
            select(ProblemVersion).where(
                ProblemVersion.problem_id == problem.id, ProblemVersion.content_hash == content_hash
            )
        )
        if not current:
            next_number = (
                db.scalar(
                    select(func.max(ProblemVersion.version_number)).where(
                        ProblemVersion.problem_id == problem.id
                    )
                )
                or 0
            ) + 1
            current = ProblemVersion(
                problem_id=problem.id,
                version_number=next_number,
                content_hash=content_hash,
                **version_payload,
            )
            db.add(current)
            db.flush()
            if next_number > 1:
                result["updated_problem_versions"] += 1
        problem.current_version_id = current.id

    db.flush()
    problem_by_key = {p.external_key: p for p in db.scalars(select(Problem)).all()}
    version_by_problem = {
        v.problem_id: v
        for v in db.scalars(
            select(ProblemVersion).where(
                ProblemVersion.id.in_([p.current_version_id for p in problem_by_key.values()])
            )
        ).all()
    }

    for item in bundle["exams"]:
        exam = db.scalar(select(Exam).where(Exam.slug == item["slug"]))
        if not exam:
            exam = Exam(
                slug=item["slug"],
                title=item["title"],
                time_limit_seconds=item["time_limit_seconds"],
                state=item.get("state", "published"),
            )
            db.add(exam)
            db.flush()
            result["created_exams"] += 1
        else:
            exam.title = item["title"]
            exam.time_limit_seconds = item["time_limit_seconds"]
            exam.state = item.get("state", "published")
        version_payload = {
            "title": item["title"],
            "time_limit_seconds": item["time_limit_seconds"],
            "items": [
                {
                    "sequence": row["sequence"],
                    "problem_external_key": row["problem_external_key"],
                    "problem_version_id": version_by_problem[
                        problem_by_key[row["problem_external_key"]].id
                    ].id,
                    "points": row["points"],
                }
                for row in item["items"]
            ],
        }
        content_hash = digest(version_payload)
        current = db.scalar(
            select(ExamVersion).where(
                ExamVersion.exam_id == exam.id, ExamVersion.content_hash == content_hash
            )
        )
        if not current:
            next_number = (
                db.scalar(select(func.max(ExamVersion.version_number)).where(ExamVersion.exam_id == exam.id))
                or 0
            ) + 1
            current = ExamVersion(
                exam_id=exam.id,
                version_number=next_number,
                title=item["title"],
                time_limit_seconds=item["time_limit_seconds"],
                content_hash=content_hash,
            )
            db.add(current)
            db.flush()
            for row in item["items"]:
                problem = problem_by_key[row["problem_external_key"]]
                db.add(
                    ExamVersionItem(
                        exam_version_id=current.id,
                        problem_id=problem.id,
                        problem_version_id=version_by_problem[problem.id].id,
                        sequence=row["sequence"],
                        points=row["points"],
                    )
                )
            if next_number > 1:
                result["updated_exam_versions"] += 1
        exam.current_version_id = current.id
    db.commit()
    return result
