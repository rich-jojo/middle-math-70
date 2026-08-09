# 중등 수학 70 중앙 플랫폼

FastAPI + PostgreSQL 기반 계정형 수학 연습 플랫폼입니다. 기존 단일 HTML CBT의 25문항은 `content/bundles/math70-v2.json`으로 이관했고, 기존 PDF 계약은 그대로 유지합니다.

- `middle-math-70-exam.pdf`
- `middle-math-70-solutions.pdf`

solved.ac의 문제 난이도/티어/첫 풀이 보상 방식에서 아이디어를 얻었지만, 이미지 파일, 로고, CSS, 브랜드 표현은 복사하지 않았습니다. 티어 배지는 이 저장소에서 새로 만든 SVG입니다.

## 아키텍처

- Python 3.11, FastAPI, SQLAlchemy 2, Alembic
- PostgreSQL 16, psycopg 3
- 같은 출처에서 HTML UI와 `/api` 제공
- 운영 세션은 HttpOnly Secure SameSite=Lax 쿠키
- 쿠키에는 불투명 랜덤 토큰만 저장하고 DB에는 SHA-256 토큰 해시만 저장
- 비밀번호는 Argon2id 해시만 저장
- mutating cookie-auth 요청은 세션 바운드 `X-CSRF-Token` 필요
- 관리자 권한은 일반 가입으로 부여되지 않으며 `mm70 bootstrap-admin` 1회성 CLI로만 생성
- 사용자 이름은 NFKC-trimmed 표시 이름을 보존하고, 로그인/중복 검사는 NFKC+casefold 키로 수행
- 가입과 로그인은 trusted client IP 기준으로 서버 rate limit 적용
- 문제 목록은 grade/semester/unit/level/solved 필터와 사용자별 solved 상태를 반환
- 모의고사 응시는 `started_at`/`deadline_at` 서버 시간을 가지며, 제출 뒤 autosave는 거부됨
- 제출 idempotency key는 첫 제출 결과/리뷰 snapshot을 재사용하고, 다른 key 또는 제출 후 답안 변경은 409
- `/api/profile`은 XP, 티어, solve count, 최근 응시 요약을 반환

## 로컬 실행

```bash
uv sync --extra test
cp .env.example .env
# .env의 POSTGRES_PASSWORD, MM70_SECRET_KEY 값을 새 값으로 교체
docker-compose up --build
```

앱은 기본적으로 `http://127.0.0.1:8000`에 바인딩됩니다. Postgres는 호스트 포트로 공개하지 않고 Compose 내부 네트워크에만 노출됩니다.

관리자 생성:

```bash
docker-compose exec -T app mm70 bootstrap-admin --username '관리자' < /secure/path/admin-password
```

비대화형 실행에서는 비밀번호를 stdin으로 읽습니다. 비밀번호를 명령 인자나 shell history에 넣지 마세요. 이미 존재하는 일반 사용자는 자동 승격하지 않으며 운영자 부트스트랩으로 생성한 계정만 관리자입니다.

## 현재 self-host 운영

- 운영 DB: 전용 PostgreSQL 16 Compose volume
- DB 포트: 외부 비공개
- 앱: `127.0.0.1:8000` 전용 바인딩
- 공개 경로: Cloudflare Tunnel HTTPS
- 상시 실행: `ops/systemd/middle-math-70-compose.service`, `middle-math-70-tunnel.service`
- 백업: 별도 디스크 `/srv/ssd/backups/middle-math-70`, 매일 03:20 KST, 30일 보관

설치 예시:

```bash
mkdir -p ~/.config/systemd/user
cp ops/systemd/*.service ops/systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now middle-math-70-compose.service middle-math-70-tunnel.service middle-math-70-backup.timer
```

현재 tunnel 주소는 `runtime/public-url.txt`에 원자적으로 기록됩니다. Quick Tunnel은 계정 없는 즉시 배포 경로라 URL·가용성 보장이 없으며, 요구가 커지면 named tunnel 또는 관리형 호스팅으로 전환합니다.

## 콘텐츠 가져오기

번들 형식은 `content/schema/problem-bundle-v1.schema.json`입니다. 기존 25문항 번들과 PDF 경로는 그대로 유지합니다. 새 문제/시험은 관리자 API/UI에서 초안 포함 목록, 문제 생성, immutable next version 생성, publish/current version 지정, 시험 생성, ordered problem-version 기반 immutable exam version 생성, publish/current exam version 지정을 할 수 있습니다. JSON 번들 import도 유지됩니다.

```bash
uv run mm70 import-bundle content/bundles/math70-v2.json --dry-run
uv run mm70 import-bundle content/bundles/math70-v2.json
```

검증은 다음을 잡습니다.

- 번들 내부 중복 external key
- 난이도 1..30 범위 위반
- 객관식 정답 인덱스와 보기 불일치
- 단답/과정형 accepted 누락
- 시험 item의 누락된 problem ref
- 시험 sequence 중복

`scripts/extract_legacy_bundle.mjs`는 기존 `index.html`에서 25문항을 다시 추출하는 재현용 도구입니다.

## 테스트

```bash
uv run --extra test python -m pytest -q tests/test_auth_security.py tests/test_content_exam_xp.py tests/test_admin_postgres_e2e.py -m 'not postgres and not e2e'
```

실제 PostgreSQL 통합 테스트:

```bash
docker-compose up -d postgres
TEST_DATABASE_URL='postgresql+psycopg://mm70:mm70_dev_password_change_me@127.0.0.1:5432/mm70' \
uv run --extra test python -m pytest -q tests/test_admin_postgres_e2e.py -m postgres
```

브라우저 E2E:

```bash
uv run --extra test python -m pytest -q tests/test_admin_postgres_e2e.py -m e2e
```

전체 검증:

```bash
uv run ruff check .
uv run --extra test python -m pytest -q -m 'not postgres and not e2e'
TEST_DATABASE_URL='postgresql+psycopg://mm70:mm70test@127.0.0.1:55432/mm70test' uv run --extra test python -m pytest -q tests/test_admin_postgres_e2e.py -m postgres
uv run --extra test python -m pytest -q tests/test_admin_postgres_e2e.py -m e2e
TEST_DATABASE_URL='postgresql+psycopg://mm70:mm70test@127.0.0.1:55432/mm70test' uv run --extra test python -m pytest -q
docker-compose build
MM70_SECRET_KEY=smoke-secret MM70_SECURE_COOKIES=false docker-compose up -d
curl -fsS http://127.0.0.1:8000/health
uv run --extra test python browser_test.py
```

기존 정적 CBT 회귀 테스트는 참고용으로 남겨 두었습니다.

```bash
uv run --extra test python browser_test.py
```

## 백업과 복구

```bash
scripts/backup.sh
scripts/restore.sh backups/mm70-YYYYMMDD-HHMMSS.dump
```

백업은 `pg_dump -Fc`, 복구는 `pg_restore --clean --if-exists`를 사용합니다.

## TLS/Reverse Proxy

`cloudflared.example.yml`은 예시 파일입니다. 실제 tunnel id, credentials, hostname은 배포 환경에서 별도로 관리해야 하며 저장소에 넣지 않습니다. TLS 뒤 운영에서는 `MM70_SECURE_COOKIES=true`를 유지하세요.
