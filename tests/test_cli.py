from __future__ import annotations

from app.cli import auto_import_bundle_paths


def test_auto_import_bundle_paths_loads_only_the_v3_bundle(monkeypatch) -> None:
    monkeypatch.setenv("MM70_AUTO_IMPORT_BUNDLES", " content/bundles/math70-v3-hard.json ")
    monkeypatch.setenv("MM70_AUTO_IMPORT_BUNDLE", "old-single-bundle.json")

    assert auto_import_bundle_paths() == ["content/bundles/math70-v3-hard.json"]


def test_old_single_bundle_environment_variable_is_not_supported(monkeypatch) -> None:
    monkeypatch.delenv("MM70_AUTO_IMPORT_BUNDLES", raising=False)
    monkeypatch.setenv("MM70_AUTO_IMPORT_BUNDLE", "old-single-bundle.json")

    assert auto_import_bundle_paths() == []
