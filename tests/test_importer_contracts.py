from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.importer import clean_html, load_bundle


def _legacy_bundle() -> dict:
    return json.loads(Path("content/bundles/math70-v2.json").read_text(encoding="utf-8"))


def _write(tmp_path: Path, bundle: dict) -> Path:
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_bundle_rejects_noncontiguous_exam_sequences(tmp_path: Path) -> None:
    bundle = _legacy_bundle()
    bundle["exams"][0]["items"][-1]["sequence"] = 26

    with pytest.raises(ValueError, match="contiguous"):
        load_bundle(_write(tmp_path, bundle))


def test_load_bundle_rejects_duplicate_choice_options(tmp_path: Path) -> None:
    bundle = _legacy_bundle()
    problem = next(item for item in bundle["problems"] if item["answer_type"] == "choice")
    problem["choices"][1] = problem["choices"][0]

    with pytest.raises(ValueError, match="choice options must be unique"):
        load_bundle(_write(tmp_path, bundle))


def test_load_bundle_rejects_duplicate_process_rubric_tokens(tmp_path: Path) -> None:
    bundle = deepcopy(_legacy_bundle())
    problem = next(item for item in bundle["problems"] if item["answer_type"] == "process")
    problem["answer_spec"]["partial"] = [
        {"points": 1, "tokens": ["x=1"]},
        {"points": 1, "tokens": ["x=1"]},
    ]

    with pytest.raises(ValueError, match="rubric tokens must be unique"):
        load_bundle(_write(tmp_path, bundle))


def test_clean_html_preserves_accessible_data_tables() -> None:
    source = (
        '<table><caption>값별 도수</caption><thead><tr><th scope="col">값</th></tr></thead>'
        "<tbody><tr><td>1</td></tr></tbody></table>"
    )

    cleaned = clean_html(source)

    assert "<table>" in cleaned
    assert "<caption>값별 도수</caption>" in cleaned
    assert '<th scope="col">' in cleaned
    assert "<tbody>" in cleaned
