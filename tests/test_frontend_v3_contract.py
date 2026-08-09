from pathlib import Path

from app.main import APP_CSS, APP_HTML, APP_JS, LANDING_HTML

ROOT = Path(__file__).resolve().parents[1]


def test_exam_ui_is_autosave_only_without_manual_save_control():
    assert 'id="save"' not in APP_JS
    assert ">저장</button>" not in APP_JS
    assert "자동 저장" in APP_JS
    assert "debounceSave(item)" in APP_JS


def test_public_and_app_shells_expose_accessible_navigation():
    assert 'class="skip-link"' in LANDING_HTML
    assert 'class="skip-link"' in APP_HTML
    assert 'aria-label="주요 메뉴"' in APP_HTML
    assert "setAttribute('aria-current', 'page')" in APP_JS


def test_legacy_frontend_and_v2_bundle_are_removed():
    assert not (ROOT / "legacy.html").exists()
    assert not (ROOT / "content/bundles/math70-v2.json").exists()
    assert not (ROOT / "scripts/extract_legacy_bundle.mjs").exists()
    assert not (ROOT / "middle-math-70-exam.pdf").exists()
    assert not (ROOT / "middle-math-70-solutions.pdf").exists()
    launcher = (ROOT / "index.html").read_text(encoding="utf-8")
    app_source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "legacy" not in launcher.lower()
    assert "middle-math-70-exam.pdf" not in app_source
    assert "middle-math-70-solutions.pdf" not in app_source
    assert "math70-v2" not in compose
    assert "MM70_AUTO_IMPORT_BUNDLES: content/bundles/math70-v3-hard.json" in compose


def test_frontend_uses_distinctive_exam_workspace_tokens():
    css = APP_CSS
    assert "--canvas:#f3f6f2" in css.lower()
    assert "--accent:#246457" in css.lower()
    assert "font-variant-numeric:tabular-nums" in css.lower()
    assert "prefers-reduced-motion" in css
