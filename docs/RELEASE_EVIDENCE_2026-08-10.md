# Self-host public release evidence — 2026-08-10

- `uv run ruff check .`: `All checks passed!`
- `uv run --extra test python -m pytest -q`: `15 passed, 2 skipped`; the two PostgreSQL markers were run separately against a temporary PostgreSQL 16 instance and returned `2 passed`.
- `uv run --extra test python browser_test.py`: `ALL TESTS PASSED`.
- Legacy bundle shadow comparison: 25 problems, choice/text/process distribution `20/3/2`, 100 total points, and zero mismatches in body/SVG/choices/answers/explanation/trap/retry text.
- Public HTTPS E2E: Unicode username and one-character password signup, answer/explanation secrecy before submit, first-solve XP, exam autosave, logout/login resume, 100-point submit, idempotent replay, profile and leaderboard all passed.
- Public E2E observed: `problems=25`, `score=100`, `solve_count=25`, `xp=2000`.
- Mobile visual/E2E at 390x844: landing, dashboard, 25-card problem bank, problem detail and exam workstation had no horizontal overflow or severe browser console errors. The exam palette is collapsed by default on mobile and open by default on desktop.
- Compose health: app and PostgreSQL both healthy; app bound to `127.0.0.1:8000`, PostgreSQL not published to the host.
- Restore drill: `pg_dump -Fc` restored into a new temporary database with `users=1`, `problems=25`, `exams=1`, `admins=1`; dump size 46,835 bytes.
- `docker-compose config --quiet`, `systemd-analyze --user verify ops/systemd/*.service ops/systemd/*.timer`, Python compile and shell syntax checks passed.
