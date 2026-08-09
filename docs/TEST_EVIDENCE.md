# TEST EVIDENCE

## RED

```bash
uv run --extra test pytest -q tests/test_auth_security.py tests/test_content_exam_xp.py tests/test_admin_postgres_e2e.py -m 'not postgres and not e2e'
```

Observed:

```text
ImportError while loading conftest ...
E   ModuleNotFoundError: No module named 'app'
```

```bash
uv run --extra test python -m pytest -q tests/test_auth_security.py tests/test_content_exam_xp.py tests/test_admin_postgres_e2e.py -m 'not postgres and not e2e'
```

Observed after initial app scaffold:

```text
E   TypeError: 'type' must be a Type (got str).
```

Observed after fixing Argon2id enum:

```text
7 failed, 2 deselected
sqlite3.OperationalError: no such table: users
ValueError: Attribute 'choices' does not accept objects of type <class 'list'>
```

## GREEN

```bash
uv run --extra test python -m pytest -q tests/test_auth_security.py tests/test_content_exam_xp.py tests/test_admin_postgres_e2e.py -m 'not postgres and not e2e'
```

Observed:

```text
7 passed, 2 deselected, 1 warning in 1.92s
```

## FINAL VERIFICATION

```bash
uv run ruff check .
```

Observed:

```text
All checks passed!
```

```bash
uv run --extra test python -m pytest -q tests/test_auth_security.py tests/test_content_exam_xp.py tests/test_admin_postgres_e2e.py -m 'not postgres and not e2e'
```

Observed:

```text
7 passed, 2 deselected, 1 warning in 2.50s
```

```bash
uv run --extra test python -m pytest -q tests/test_admin_postgres_e2e.py -m e2e
```

Observed:

```text
1 passed, 2 deselected, 1 warning in 2.12s
```

```bash
TEST_DATABASE_URL='postgresql+psycopg://mm70:mm70test@127.0.0.1:55432/mm70test' \
uv run --extra test python -m pytest -q tests/test_admin_postgres_e2e.py -m postgres
```

Observed:

```text
1 passed, 2 deselected, 1 warning in 0.96s
```

```bash
TEST_DATABASE_URL='postgresql+psycopg://mm70:mm70test@127.0.0.1:55432/mm70test' \
uv run --extra test python -m pytest -q
```

Observed:

```text
9 passed, 1 warning in 3.89s
```

```bash
uv run --extra test python browser_test.py
```

Observed:

```text
ALL TESTS PASSED
Screenshots: /home/jojo/worktrees/middle-math-70/feat-central-bank-auth/test-artifacts
```

```bash
docker-compose build
```

Observed:

```text
Successfully built f572f7a52813
Successfully tagged feat-central-bank-auth-app:latest
```

After adding `PYTHONPATH=/app` and PDF `HEAD` support, rebuild:

```bash
docker-compose build app
```

Observed:

```text
Successfully built 6f062a41ec5e
Successfully tagged feat-central-bank-auth-app:latest
```

```bash
docker-compose up -d
curl -fsS http://127.0.0.1:8000/health
```

Observed:

```text
{"ok":true}
```

Compose smoke:

```bash
curl -sS -o /tmp/mm70-gate2.json -w 'gate=%{http_code}\n' http://127.0.0.1:8000/api/problems
curl -sS -c /tmp/mm70-cookies2.txt -H 'Content-Type: application/json' \
  -d '{"username":"compose 학생 2","password":"pw"}' http://127.0.0.1:8000/api/signup
curl -sS -b /tmp/mm70-cookies2.txt http://127.0.0.1:8000/api/problems
curl -sS -b /tmp/mm70-cookies2.txt http://127.0.0.1:8000/api/exams
curl -fsSI http://127.0.0.1:8000/middle-math-70-exam.pdf
```

Observed:

```text
gate=401
signup true compose 학생 2 false
problems 25 1. 중1-2 기본도형 false
exams 1 math70-v2 7200
HTTP/1.1 200 OK
content-length: 771271
content-type: application/pdf
```

## AUDIT GAP FIXES 2026-08-10

### RED

```bash
uv run --extra test python -m pytest -q tests/test_auth_security.py tests/test_content_exam_xp.py tests/test_admin_postgres_e2e.py -m 'not postgres and not e2e'
```

Observed after adding focused failing tests:

```text
FAILED tests/test_auth_security.py::test_signup_normalizes_unicode_rejects_duplicates_and_never_grants_admin
FAILED tests/test_auth_security.py::test_username_normalized_key_collides_case_and_fullwidth_but_preserves_display
FAILED tests/test_auth_security.py::test_signup_rate_limit_uses_trusted_client_ip_and_returns_korean_429
FAILED tests/test_content_exam_xp.py::test_problem_list_returns_solved_state_and_filters_for_current_user
FAILED tests/test_content_exam_xp.py::test_profile_endpoint_reports_xp_solve_count_and_attempt_history
FAILED tests/test_content_exam_xp.py::test_exam_attempt_freezes_snapshot_autosaves_grades_and_does_not_double_award_xp
FAILED tests/test_content_exam_xp.py::test_attempt_save_and_submit_validate_sequences_and_deadline
FAILED tests/test_admin_postgres_e2e.py::test_admin_problem_exam_versioning_validation_and_attempt_snapshots
8 failed, 5 passed, 3 deselected, 1 warning in 2.69s
```

### GREEN

```bash
uv run --extra test python -m pytest -q tests/test_auth_security.py tests/test_content_exam_xp.py tests/test_admin_postgres_e2e.py -m 'not postgres and not e2e'
```

Observed:

```text
13 passed, 3 deselected, 1 warning in 4.16s
```

After adding the bundle/PDF contract regression:

```bash
uv run --extra test python -m pytest -q -m 'not postgres and not e2e'
```

Observed:

```text
14 passed, 3 deselected, 1 warning in 4.86s
```

### FINAL VERIFICATION

```bash
uv run ruff check .
```

Observed:

```text
All checks passed!
```

```bash
uv run --extra test python -m pytest -q tests/test_admin_postgres_e2e.py -m e2e
```

Observed:

```text
1 passed, 4 deselected, 1 warning in 8.03s
```

Temporary PostgreSQL test database:

```bash
docker rm -f mm70-pg-test >/dev/null 2>&1 || true
docker run -d --name mm70-pg-test -e POSTGRES_DB=mm70test -e POSTGRES_USER=mm70 -e POSTGRES_PASSWORD=mm70test -p 127.0.0.1:55432:5432 postgres:16 >/tmp/mm70-pg-test.cid
for i in $(seq 1 40); do
  if docker exec mm70-pg-test pg_isready -U mm70 -d mm70test >/dev/null 2>&1; then
    echo ready
    exit 0
  fi
  sleep 1
done
docker logs mm70-pg-test
echo not-ready
exit 1
```

Observed:

```text
ready
```

```bash
TEST_DATABASE_URL='postgresql+psycopg://mm70:mm70test@127.0.0.1:55432/mm70test' uv run --extra test python -m pytest -q tests/test_admin_postgres_e2e.py -m postgres
```

Observed:

```text
2 passed, 3 deselected, 1 warning in 1.36s
```

```bash
TEST_DATABASE_URL='postgresql+psycopg://mm70:mm70test@127.0.0.1:55432/mm70test' uv run --extra test python -m pytest -q
```

Observed:

```text
17 passed, 1 warning in 13.21s
```

```bash
docker-compose build
```

Observed:

```text
Successfully built 7ee6e9725707
Successfully tagged feat-central-bank-auth-app:latest
```

```bash
MM70_SECRET_KEY=smoke-secret MM70_SECURE_COOKIES=false docker-compose up -d
curl -fsS http://127.0.0.1:8000/health
```

Observed:

```text
{"ok":true}
```

Compose smoke:

```bash
curl -sS -o /tmp/mm70-gate.json -w 'gate=%{http_code}\n' http://127.0.0.1:8000/api/problems
curl -sS -c /tmp/mm70-cookies.txt -H 'Content-Type: application/json' -d '{"username":"compose 학생","password":"pw"}' http://127.0.0.1:8000/api/signup
curl -sS -b /tmp/mm70-cookies.txt http://127.0.0.1:8000/api/problems
curl -sS -b /tmp/mm70-cookies.txt http://127.0.0.1:8000/api/exams
curl -fsSI http://127.0.0.1:8000/middle-math-70-exam.pdf
```

Observed:

```text
gate=401
signup True compose 학생 False
problems 25 1. 중1-2 기본도형 False
exams 1 math70-v2 7200
HTTP/1.1 200 OK
content-length: 771271
content-type: application/pdf
```

```bash
uv run --extra test python browser_test.py
```

Observed:

```text
ALL TESTS PASSED
Screenshots: /home/jojo/worktrees/middle-math-70/feat-central-bank-auth/test-artifacts
```
