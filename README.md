# 수학 70

FastAPI + PostgreSQL 기반 중등 수학 학습 서비스입니다. 현재 콘텐츠는 고난도 25문항 신버전 `content/bundles/math70-v3-hard.json` 하나로 통합되어 있습니다.

- 범위: 중1-2, 중2-1, 중2-2
- 시험: 25문항, 100점, 120분
- 객관식 20문항, 단답형 3문항, 과정형 2문항
- 문제 풀이, 모의고사, 응시 복원, 서버 채점, XP, 티어, 순위
- 답안 선택과 입력 시 서버 자동 저장, 브라우저 복구 캐시

solved.ac의 난이도, 티어, 첫 풀이 보상 방식에서 개념만 참고했습니다. 이미지, 로고, CSS, 브랜드 자산은 복제하지 않았고 티어 배지는 이 저장소에서 제작한 SVG입니다.

## 아키텍처

- Python 3.11, FastAPI, SQLAlchemy 2, Alembic
- PostgreSQL 16, psycopg 3
- 같은 출처에서 HTML UI와 `/api` 제공
- HttpOnly Secure SameSite=Lax 세션 쿠키
- Argon2id 비밀번호 해시
- 세션 바운드 `X-CSRF-Token`
- 문제 revision, 시험 version, 응시 snapshot 불변 모델
- 제출 idempotency와 최초 풀이 XP 고유 제약
- 운영 DB 포트 외부 비공개

## 로컬 실행

```bash
uv sync --extra test
cp .env.example .env
# .env의 POSTGRES_PASSWORD와 MM70_SECRET_KEY를 새 값으로 교체
docker-compose up --build
```

앱은 기본적으로 `http://127.0.0.1:8000`에 바인딩됩니다. PostgreSQL은 Compose 내부 네트워크에만 노출됩니다.

관리자 생성:

```bash
docker-compose exec -T app mm70 bootstrap-admin --username '관리자' < /secure/path/admin-password
```

비밀번호는 stdin으로 전달합니다. 일반 가입 사용자는 자동으로 관리자가 되지 않습니다.

## 운영

- 공개 고정 진입점: `https://rich-jojo.github.io/middle-math-70/`
- 앱: `127.0.0.1:8000`
- 공개 경로: Cloudflare Tunnel HTTPS
- 서비스: `ops/systemd/middle-math-70-compose.service`, `middle-math-70-tunnel.service`
- 백업: `/srv/ssd/backups/middle-math-70`, 매일 03:20 KST, 30일 보관

Quick Tunnel 주소는 바뀔 수 있습니다. Watchdog이 `public-endpoint.json`을 갱신하므로 사용자는 GitHub Pages 고정 주소로 접속합니다.

## 콘텐츠 가져오기

```bash
uv run mm70 import-bundle content/bundles/math70-v3-hard.json --dry-run
uv run mm70 import-bundle content/bundles/math70-v3-hard.json
uv run python scripts/verify_v3_math.py
```

번들 importer는 다음을 검증합니다.

- 문제 키 중복
- 난이도 범위
- 객관식 정답 인덱스와 중복 보기
- 단답형 및 과정형 accepted 답안
- 과정형 rubric token 중복
- 시험 item의 문제 참조
- 시험 문항 sequence 연속성
- 25문항, 100점 계약

## 검증

빠른 검사:

```bash
uv run ruff check .
uv run ruff format --check .
uv run --extra test python -m pytest -q -m 'not postgres and not e2e'
uv run python scripts/verify_v3_math.py
```

실제 PostgreSQL 검사:

```bash
TEST_DATABASE_URL='postgresql+psycopg://mm70:mm70test@127.0.0.1:55432/mm70test' \
uv run --extra test python -m pytest -q tests/test_admin_postgres_e2e.py -m postgres
```

브라우저 E2E:

```bash
uv run --extra test python -m pytest -q tests/test_admin_postgres_e2e.py -m e2e
```

Docker smoke:

```bash
docker-compose build app
MM70_SECRET_KEY=smoke-secret MM70_SECURE_COOKIES=false docker-compose up -d
curl -fsS http://127.0.0.1:8000/health
```

## 백업과 복구

```bash
scripts/backup.sh
scripts/restore.sh backups/mm70-YYYYMMDD-HHMMSS.dump
```

백업은 `pg_dump -Fc`, 복구는 `pg_restore --clean --if-exists`를 사용합니다.
