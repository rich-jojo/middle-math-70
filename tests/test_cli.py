from __future__ import annotations

from app.cli import auto_import_bundle_paths


def test_auto_import_bundle_paths_supports_multiple_bundles(monkeypatch) -> None:
    monkeypatch.setenv(
        "MM70_AUTO_IMPORT_BUNDLES",
        " content/bundles/math70-v2.json, content/bundles/math70-v3-hard.json ",
    )
    monkeypatch.setenv("MM70_AUTO_IMPORT_BUNDLE", "ignored.json")

    assert auto_import_bundle_paths() == [
        "content/bundles/math70-v2.json",
        "content/bundles/math70-v3-hard.json",
    ]


def test_auto_import_bundle_paths_keeps_legacy_single_bundle(monkeypatch) -> None:
    monkeypatch.delenv("MM70_AUTO_IMPORT_BUNDLES", raising=False)
    monkeypatch.setenv("MM70_AUTO_IMPORT_BUNDLE", "content/bundles/math70-v2.json")

    assert auto_import_bundle_paths() == ["content/bundles/math70-v2.json"]
