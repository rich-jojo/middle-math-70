from __future__ import annotations

import re
from collections import Counter

from app.grading import grade_points, normalize_answer
from app.importer import import_bundle, load_bundle
from app.main import APP_JS
from app.models import ProblemVersion
from tests.conftest import signup_and_login

V3_PATH = "content/bundles/math70-v3-hard.json"


def test_process_answer_ui_uses_multiline_textarea() -> None:
    assert "item.answer_type === 'process'" in APP_JS
    assert '<textarea id="examText"' in APP_JS


def test_v3_hard_exam_contract() -> None:
    bundle = load_bundle(V3_PATH)
    problems = bundle["problems"]
    exam = bundle["exams"][0]
    by_key = {problem["external_key"]: problem for problem in problems}

    assert len(problems) == 25
    assert len(exam["items"]) == 25
    assert [item["sequence"] for item in exam["items"]] == list(range(1, 26))
    assert sum(item["points"] for item in exam["items"]) == 100
    assert exam["time_limit_seconds"] == 7200
    assert exam["slug"] == "math70-v3-hard"
    assert exam["state"] == "published"
    assert all(problem["state"] == "published" for problem in problems)

    ordered = [by_key[item["problem_external_key"]] for item in exam["items"]]
    assert Counter(problem["semester"] for problem in ordered[:18]) == {
        "1-2": 6,
        "2-1": 6,
        "2-2": 6,
    }
    assert Counter(problem["answer_type"] for problem in ordered) == {
        "choice": 20,
        "text": 3,
        "process": 2,
    }
    assert all("안전망 72점" in problem["tags"] for problem in ordered[:18])
    assert all("변별" in problem["tags"] for problem in ordered[18:])

    choice = [problem for problem in ordered if problem["answer_type"] == "choice"]
    assert Counter(problem["answer_spec"]["correct_index"] for problem in choice) == {
        0: 4,
        1: 4,
        2: 4,
        3: 4,
        4: 4,
    }
    assert all(len(problem["choices"]) == 5 for problem in choice)
    assert all(len(set(problem["choices"])) == 5 for problem in choice)

    for problem in ordered:
        svg = problem.get("diagram_svg", "")
        if svg:
            assert 'role="img"' in svg
            label = re.search(r'aria-label="([^"]+)"', svg)
            assert label and re.search(r"[가-힣]", label.group(1))

    for problem in (item for item in ordered if item["answer_type"] == "process"):
        spec = problem["answer_spec"]
        assert sum(part["points"] for part in spec["partial"]) == 4
        token_sets = [{normalize_answer(token) for token in part["tokens"]} for part in spec["partial"]]
        all_tokens = [token for tokens in token_sets for token in tokens]
        assert len(all_tokens) == len(set(all_tokens))
        assert not any(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", token) for token in all_tokens)
        assert not any(normalize_answer(accepted) in {"8", "320", "320g"} for accepted in spec["accepted"])


def test_v3_import_attempt_and_perfect_submission(client, sqlite_session) -> None:
    first = import_bundle(sqlite_session, V3_PATH, dry_run=False)
    second = import_bundle(sqlite_session, V3_PATH, dry_run=False)
    auth = signup_and_login(client, "v3-solver", "pw")

    assert first["created_problems"] == 25
    assert first["created_exams"] == 1
    assert second["created_problems"] == 0
    assert second["created_exams"] == 0
    exams = client.get("/api/exams").json()["exams"]
    assert [exam["slug"] for exam in exams] == ["math70-v3-hard"]

    started = client.post(
        "/api/exams/math70-v3-hard/attempts",
        headers={"X-CSRF-Token": auth["csrf"]},
    )
    assert started.status_code == 201
    attempt_id = started.json()["attempt_id"]
    attempt = client.get(f"/api/attempts/{attempt_id}").json()
    assert len(attempt["items"]) == 25
    assert "answer_spec" not in str(attempt)
    assert "explanation_html" not in str(attempt)

    answers = {}
    for item in attempt["items"]:
        version = sqlite_session.get(ProblemVersion, item["problem_version_id"])
        if version.answer_type == "choice":
            answers[item["sequence"]] = {"choice": version.answer_spec["correct_index"]}
        else:
            answers[item["sequence"]] = {"text": version.answer_spec["accepted"][0]}

    final_version = sqlite_session.get(ProblemVersion, attempt["items"][-1]["problem_version_id"])
    assert final_version.answer_type == "process"
    assert grade_points(final_version, {"text": "8"}, 4) == 0
    assert (
        grade_points(
            final_version,
            {"text": ("f(x)=-2x+4이다; g(x)=-(2/3)x+4이다; x절편 2, 6을 얻고; 넓이=8이다")},
            4,
        )
        == 4
    )
    assert (
        grade_points(
            final_version,
            {"text": ("f(x)=-2x+4가 아니고 g(x)=-(2/3)x+4도 아니며 x절편은 2와 6이 아니고 넓이=8이 아니다")},
            4,
        )
        == 0
    )

    q24_item = next(item for item in attempt["items"] if item["sequence"] == 24)
    q24 = sqlite_session.get(ProblemVersion, q24_item["problem_version_id"])
    assert q24 is not None
    assert (
        grade_points(
            q24,
            {
                "text": (
                    "두 각은 직각이 아니고 AB=CD도 아니며 두 선은 평행하지 않고 "
                    "RHA 합동이 아니므로 AE=CF가 아니다"
                )
            },
            4,
        )
        == 0
    )

    submitted = client.post(
        f"/api/attempts/{attempt_id}/submit",
        headers={"X-CSRF-Token": auth["csrf"]},
        json={"answers": answers, "idempotency_key": "v3-perfect"},
    )
    assert submitted.status_code == 200
    assert submitted.json()["score"] == 100
    assert len(submitted.json()["review"]) == 25
