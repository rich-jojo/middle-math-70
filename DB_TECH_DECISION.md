# 중앙 DB 기술 선택 최종 결정문

- 대상: `middle-math-70` 정적 CBT의 공개 계정·중앙 문제은행·시험·풀이 기록·XP/티어/랭킹·관리자 CRUD 확장
- 결정 상태: **PostgreSQL 채택(엔진 기준), self-host 채택(현재 운영 토폴로지)**
- 조사·가격 확인 시점: **2026-08-09 KST**
- 가격 주의: 아래 금액은 확인 시점의 공식 공개 가격이며 세금, 환율, API 실행 PaaS, 객체 저장소, 네트워크 초과 사용료는 별도다. 서비스 가격과 무료 한도는 바뀔 수 있으므로 결제 직전에 다시 확인한다.

## 0. 실제 운영 결정 — 2026-08-10

현재 예상 사용자는 1~2명이고, 상시 가동 중인 기존 호스트에 Docker와 별도 백업 디스크가 이미 있다. 운영자의 최신 결정에 따라 **PostgreSQL 16을 이 호스트에 전용 Compose stack으로 self-host**한다.

- PostgreSQL 포트는 호스트/인터넷에 publish하지 않고 Compose 내부 네트워크에서만 접근한다.
- FastAPI만 `127.0.0.1:8000`에 바인딩하고 Cloudflare Tunnel을 통해 HTTPS로 공개한다.
- Plane/Hatchet 등 다른 서비스의 DB·volume·사용자와 공유하지 않는다.
- named volume이 source of truth이며 `/srv/ssd/backups/middle-math-70`에 매일 `pg_dump -Fc` 백업한다.
- 표준 PostgreSQL, SQLAlchemy, Alembic 계약을 유지하므로 가용성·사용자 수 요구가 커지면 Neon/Render Postgres 등 관리형 PostgreSQL로 데이터와 API를 그대로 이전할 수 있다.

이 결정은 아래 조사에서 선정한 **PostgreSQL 엔진**을 바꾸지 않고, 현재 규모에서 불필요한 SaaS 인증·비용·운영 의존성만 제거한다. 아래 Neon 평가는 향후 이전 후보에 대한 조사 기록으로 보존한다.

## 1. 관리형 배포가 필요할 때의 1순위

### 관리형 1순위 — Neon PostgreSQL + 자체 API/Argon2id 인증

**현재 self-host의 향후 관리형 이전 1순위로 채택한다.**

구성은 다음과 같다.

```text
브라우저
  └─ 정적 프런트엔드(초기에는 GitHub Pages, 가능하면 사용자 도메인)
       └─ HTTPS JSON API(동일 사이트의 api 서브도메인 권장)
            ├─ 자체 사용자명/Argon2id/불투명 세션 인증
            ├─ 문제은행·시험·autosave·제출·랭킹·관리 API
            └─ Neon PostgreSQL(같은 지역, pooled TLS 연결)
                 └─ 별도 계정의 객체 저장소에 nightly pg_dump
```

API는 특정 PaaS에 결박하지 않는 컨테이너로 만든다. 현재 코드 방향에는 **FastAPI + SQLAlchemy 2 + Alembic + psycopg 3**가 자연스럽지만, DB 계약은 표준 PostgreSQL SQL로 유지한다. Neon 전용 기능은 운영(브랜치/PITR)에만 쓰고 테이블·쿼리에는 넣지 않는다.

선정 이유:

1. 이 서비스는 분석 파일이 아니라 **동시 로그인을 받는 중앙 OLTP**다. PostgreSQL MVCC는 읽기와 쓰기가 서로 막히지 않는 다중 사용자 트랜잭션 모델을 제공한다.
2. 사용자, 세션, 문제 revision, 시험 version, 답안, 최초 정답, XP ledger는 강한 FK·UNIQUE·트랜잭션이 필요한 관계형 데이터다.
3. 문제 본문은 정규화된 컬럼과 `jsonb`를 함께 쓰면 된다. PostgreSQL은 유효 JSON을 강제하고, `jsonb` 처리·인덱싱을 지원한다.
4. Neon은 표준 PostgreSQL 호환이므로 향후 Supabase, RDS, Cloud SQL, 자가 호스팅 PostgreSQL로 옮길 수 있다. D1/Turso보다 DB 계층의 재작성 위험이 작다.
5. 지금은 사용자가 거의 없어도 scale-to-zero와 무료 한도로 시작할 수 있고, 3년 뒤 사용자·autosave·관리 기능이 늘어도 DB를 다시 고를 필요가 없다.
6. 이메일 중심 BaaS Auth를 억지로 쓰지 않고, 요구한 느슨한 Unicode 아이디와 자체 Argon2id를 API에서 정확히 구현할 수 있다.

### 2순위 — Cloudflare Workers + D1 + 자체 인증

**비용·운영 단순성이 장기 SQL 확장성보다 더 중요해지는 경우에만 선택한다.**

D1은 무료/저비용, Worker와의 짧은 경로, 자동 Time Travel, SQLite 호환 dump가 장점이다. `batch()`는 전체 rollback되는 SQL 트랜잭션을 제공하므로 현재 규모의 autosave·제출도 구현할 수 있다.

그러나 단일 D1 DB는 공식 문서상 본질적으로 single-threaded이고 쿼리를 한 번에 하나씩 처리한다. DB당 유료 최대 10 GB, row/BLOB 최대 2 MB, SQL 실행 최대 30초라는 제품 경계도 있다. 현재는 충분하지만 3년 동안 통계, 관리자 검색, 이벤트 ledger, 대량 migration이 늘면 PostgreSQL보다 우회 설계가 많아진다. 또한 Worker 런타임에서 Argon2id를 운영하면 WASM/CPU 예산과 파라미터를 별도로 검증해야 한다.

따라서 D1은 **작고 영구히 단순한 서비스**에는 훌륭하지만, 이 프로젝트의 최종 형태에는 2순위다.

## 2. 후보 비교표

평가: `◎` 매우 적합, `○` 적합, `△` 조건부, `×` 부적합.

| 후보 | 중앙 OLTP/동시 쓰기 | 관계·트랜잭션/JSON | 자체 Unicode+Argon2 인증 | 백업·복구 | 3년 유지보수·이식성 | 비용/운영 판단 | 결론 |
|---|---:|---:|---:|---:|---:|---|---|
| **Neon PostgreSQL** | ◎ | ◎ (`FK`, `UNIQUE`, MVCC, `jsonb`) | ◎ API에서 완전 제어 | ◎ 1~30일 plan별 history + `pg_dump` | ◎ 표준 PostgreSQL, scale-to-zero, branching | Free 또는 Launch 사용량 과금, 유료 월 최소 없음 | **1순위** |
| **Cloudflare D1** | ○ 현재 규모 / △ 장기 | ○ SQLite 트랜잭션·FK; PG 기능은 없음 | ○ Worker에서 직접 구현 | ◎ Time Travel 항상 켜짐 + SQLite dump | △ DB당 10 GB, 단일 DB single-thread, Worker 결합 | Free 또는 행 read/write 기반, idle compute 0 | **2순위** |
| **Supabase PostgreSQL** | ◎ | ◎ | ○ 자체 API를 두면 가능; Supabase Auth 장점은 상당 부분 미사용 | ○ Pro 7일 daily; Free 자동 백업 없음; PITR 고가 add-on | ◎ DB는 PostgreSQL, 플랫폼 구성은 더 큼 | Free 또는 Pro $25/월부터 | 좋은 대안이나 현재 요구에는 과한 bundle |
| **Turso/libSQL** | ○ | ○ SQLite 계열, 명시 트랜잭션 지원 | ○ 자체 API 필요 | ○ plan별 PITR | △ SQLite 친화적이지만 libSQL/Turso 기능 결합 | Free, Developer $5.99, Scaler $29 확인 | D1과 유사한 조건부 대안 |
| **자가 호스팅 PostgreSQL** | ◎ | ◎ | ◎ | 운영자가 WAL/PITR·off-site를 모두 책임 | ○ DB 이식성 최고, 운영 부담 최고 | VM·백업·모니터링·보안패치 비용 별도 | 규모가 아니라 규제/통제 필요 시 |
| **단일 서버 SQLite WAL** | △ 한 writer | ○ 소규모에는 충분 | ◎ API에서 구현 | 파일 snapshot/dump를 직접 운영 | △ 단일 API 프로세스에는 단순, 수평 확장 때 재설계 | 매우 저렴하나 서버·디스크 운영 필요 | 로컬/테스트 또는 영구 단일 인스턴스용 |
| **DuckDB** | × 중앙 서비스 모델 불일치 | ○ SQL/JSON 가능 여부가 핵심 문제가 아님 | △ API로 가능하나 이점 없음 | 파일 백업 직접 운영 | × 분석용 설계를 OLTP에 억지 적용 | 라이브러리는 단순하나 운영 위험을 숨김 | **탈락** |

### 가격 스냅샷

- **Neon**: Free는 공식 plan 문서 기준 프로젝트당 100 CU-hours, 0.5 GB 저장소, 5 GB public egress, 5분 후 scale-to-zero다. Launch는 확인 시점에 compute `$0.106/CU-hour`, storage `$0.35/GB-month`, instant-restore history storage `$0.20/GB-month`이며 유료 월 최소 금액이 없다. 무료 한도 초과 시 compute가 다음 billing cycle까지 정지될 수 있으므로 공개 가입 후에는 Launch + spending limit가 안전하다.
- **D1**: Free는 5 million rows read/day, 100,000 rows written/day, 계정 총 5 GB. Workers Paid에는 월 첫 25 billion rows read, 50 million rows written, 5 GB가 포함되고 초과분은 각각 `$0.001/million rows read`, `$1.00/million rows written`, `$0.75/GB-month`다. Workers 구독 자체 비용은 이 표에 포함하지 않았다.
- **Supabase**: Free DB 500 MB이며 1주 inactivity 후 pause될 수 있다. Pro는 `$25/month`부터이고 1개 Micro compute를 상쇄하는 `$10` compute credit를 포함한다. Pro daily backup은 7일 보관한다. PITR는 확인 시점에 7일당 `$100/month` add-on이며 최소 Small compute가 필요하다.
- **Turso**: 공식 pricing snapshot 기준 Free `$0`, Developer `$5.99/month`, Scaler `$29/month`; 각각 PITR 1일/10일/30일이었다. 동적 가격 페이지에서 숫자 본문이 직접 노출되지 않는 시도가 있었으므로 **Turso를 실제 선택한다면 결제 직전 공식 페이지에서 재확인**한다.
- 자가 호스팅, SQLite, DuckDB의 DB 라이선스 비용이 낮다는 사실은 TLS, VM, 모니터링, 장애 대응, off-site backup 운영비가 0이라는 뜻이 아니다.

## 3. DuckDB가 부적합한 구체적 이유

DuckDB가 느리거나 나쁜 DB라서가 아니다. **문제에 맞지 않는다.**

1. DuckDB의 핵심 목표는 공식 설명대로 in-process **OLAP/분석 질의**다. 이 서비스의 핵심 부하는 로그인, 세션 갱신, 잦은 답안 UPSERT, 제출, 최초 정답 UNIQUE 판정, XP ledger INSERT 같은 짧은 OLTP 쓰기다.
2. 공식 concurrency 문서상 native 파일의 read-write는 기본적으로 한 writer process 안에서의 multi-thread 동시성이다. 서로 다른 프로세스의 안정적인 중앙 쓰기 DB라는 전통적인 client/server 전제가 아니다.
3. 같은 row를 동시 수정하면 optimistic conflict가 발생하고 application retry가 필요하다. 이는 OLAP read 성능을 위한 합리적 절충이지만 autosave와 제출 경합의 기본 DB로 선택할 이유는 없다.
4. DuckDB의 Quack remote protocol은 확인 시점 v1.5.2에서 beta이며 v2.0에 성숙 목표라고 명시돼 있다. 새 공개 인증 서비스의 source of truth를 미래 성숙 일정에 걸지 않는다.
5. DuckDB 공식 문서조차 안정적인 multi-process read-write 해법으로 DuckLake + PostgreSQL catalog를 언급한다. 결국 PostgreSQL을 운영할 것이라면 사용자 계정·시험 상태는 처음부터 PostgreSQL에 두는 편이 단순하다.
6. API replica, connection pooling, 표준 관리형 백업/PITR, 일반 ORM migration 생태계까지 고려하면 DuckDB 채택은 줄어드는 코드보다 새 운영 규칙을 더 많이 만든다.

**적절한 사용처**는 별도다. 향후 익명화한 풀이 이벤트를 Parquet로 export해 난이도 분석·문항 통계를 수행하는 offline/배치 분석 엔진으로 DuckDB를 쓰는 것은 좋다. 운영 DB source of truth로는 쓰지 않는다.

## 4. 인증 결정

### BaaS Auth가 아니라 자체 Argon2id

요구사항은 이메일 없이 Unicode 아이디로 가입하고, 비밀번호는 1자 이상을 허용하는 것이다. Supabase Auth 등 이메일/전화 중심 흐름을 우회해 가짜 이메일을 만드는 것보다 다음 자체 모델이 명확하다.

- `login_display`: 사용자가 입력한 표시용 아이디
- `login_key`: 애플리케이션에서 `NFC` 정규화한 로그인 key
- 아이디는 정규화 후 1~64 Unicode code point를 허용한다.
- NUL, C0/C1 control, bidi override 같은 표시·로그 위험 문자는 거부한다. 나머지 한글, 한자, emoji, 공백 등은 허용 가능하다.
- 비밀번호는 **절대 trim, case-fold, Unicode normalize하지 않는다**. 입력한 UTF-8 sequence 그대로 검증한다.
- 비밀번호는 1자 이상을 지원하되 요청 body/DoS 방지를 위해 1024 UTF-8 bytes 상한을 둔다.
- 저장은 검증된 Argon2id 라이브러리의 PHC string(`$argon2id$...`)만 사용한다. 사용자별 random salt는 라이브러리에 맡기고 원문·복호화 가능 암호는 저장하지 않는다.
- 초기 파라미터 예: memory 64 MiB, iterations 3, parallelism 1. 운영 API에서 벤치마크해 로그인 1회 목표 latency에 맞추되 파라미터를 hash 자체에 기록해 점진 rehash한다.
- 선택적으로 DB 밖 secret manager의 pepper를 추가한다.

**보안 판단:** 공개 가입에서 1자 비밀번호는 실제 계정 탈취 위험이 매우 크다. 기능 요구로 허용하더라도 UI에 `매우 약함` 경고를 표시하고 다음을 필수로 둔다.

- signup/login을 IP + normalized login key 기준 rate limit
- 지수형 지연 또는 짧은 lockout, 단 존재 여부를 숨기는 동일 오류 문구
- HttpOnly + Secure + SameSite cookie의 random opaque session token; DB에는 token hash만 저장
- session rotation, logout/revoke, 최대 수명과 idle timeout
- 이메일이 없으므로 1회용 recovery code를 발급하거나 admin-assisted reset 절차 제공
- 관리자 계정에는 1자 비밀번호 정책을 적용하지 않고 강한 비밀번호 + 별도 2차 인증 요구
- CORS origin allowlist, CSRF 방어, 감사 로그, 비밀/DB URL은 프런트 번들에 절대 포함하지 않음

GitHub Pages의 `github.io` origin과 별도 API 도메인은 브라우저의 third-party cookie 정책에 걸릴 수 있다. 안정적인 인증을 위해 프런트와 API를 `app.example.com` / `api.example.com`처럼 **같은 registrable domain**에 두거나 동일 origin reverse proxy를 사용한다.

## 5. PostgreSQL 구체 스키마

원칙은 **정체성·관계·점수는 정규화**, 문제 렌더링 payload는 **versioned `jsonb`**, 모든 게시 콘텐츠는 **불변 revision**이다.

### 5.1 핵심 테이블

```text
users
  id uuid PK
  login_display text NOT NULL
  login_key text NOT NULL UNIQUE
  password_hash text NOT NULL
  role text CHECK ('learner','admin')
  status text CHECK ('active','locked','banned')
  token_version integer NOT NULL DEFAULT 1
  created_at, updated_at timestamptz

user_sessions
  id uuid PK
  user_id uuid FK users ON DELETE CASCADE
  token_hash bytea UNIQUE NOT NULL
  created_at, last_seen_at, idle_expires_at, absolute_expires_at timestamptz
  revoked_at timestamptz NULL
  ip_prefix_hash, user_agent_hash bytea NULL

problems
  id uuid PK
  slug text UNIQUE NOT NULL
  subject_code, curriculum_code, grade_term text
  visibility text CHECK ('draft','published','retired')
  created_by uuid FK users
  created_at, updated_at timestamptz

problem_revisions
  id uuid PK
  problem_id uuid FK problems ON DELETE RESTRICT
  revision_no integer NOT NULL
  prompt jsonb NOT NULL
  answer_key jsonb NOT NULL
  solution jsonb NOT NULL
  metadata jsonb NOT NULL DEFAULT '{}'
  content_hash text NOT NULL
  published_at timestamptz NULL
  created_by uuid FK users
  created_at timestamptz
  UNIQUE(problem_id, revision_no)
  UNIQUE(problem_id, content_hash)

problem_assets
  id uuid PK
  problem_revision_id uuid FK problem_revisions ON DELETE RESTRICT
  kind text CHECK ('svg','png','pdf','other')
  object_key text NOT NULL
  sha256 text NOT NULL
  mime_type text NOT NULL
  byte_size bigint NOT NULL
  UNIQUE(problem_revision_id, object_key)

tests
  id uuid PK
  slug text UNIQUE NOT NULL
  title text NOT NULL
  visibility text CHECK ('draft','published','retired')
  created_by uuid FK users
  created_at, updated_at timestamptz

test_versions
  id uuid PK
  test_id uuid FK tests ON DELETE RESTRICT
  version_no integer NOT NULL
  state text CHECK ('draft','published','retired')
  duration_seconds integer NOT NULL
  policy jsonb NOT NULL DEFAULT '{}'
  content_hash text NOT NULL
  published_at timestamptz NULL
  UNIQUE(test_id, version_no)
  UNIQUE(test_id, content_hash)

test_version_items
  test_version_id uuid FK test_versions ON DELETE RESTRICT
  position integer NOT NULL
  problem_revision_id uuid FK problem_revisions ON DELETE RESTRICT
  points numeric(7,2) NOT NULL
  item_policy jsonb NOT NULL DEFAULT '{}'
  PRIMARY KEY(test_version_id, position)
  UNIQUE(test_version_id, problem_revision_id)
```

`prompt`에는 문항 stem, choice, 렌더링 block을 넣되 정답은 넣지 않는다. learner API는 `answer_key`와 `solution`을 제출 전 반환하지 않는다. SVG는 sanitize한 뒤 작은 inline asset만 허용하고, 일반적으로 immutable object storage key + SHA-256을 저장한다.

### 5.2 풀이·autosave·채점

```text
attempts
  id uuid PK
  user_id uuid FK users ON DELETE RESTRICT
  test_version_id uuid FK test_versions ON DELETE RESTRICT
  attempt_no integer NOT NULL
  status text CHECK ('in_progress','submitted','abandoned')
  started_at, submitted_at timestamptz
  elapsed_seconds integer
  score numeric(9,2) NULL
  max_score numeric(9,2) NULL
  submission_idempotency_key uuid NULL
  UNIQUE(user_id, test_version_id, attempt_no)
  UNIQUE(user_id, submission_idempotency_key)

attempt_answers
  attempt_id uuid FK attempts ON DELETE CASCADE
  position integer NOT NULL
  answer jsonb NOT NULL
  review_mark boolean NOT NULL DEFAULT false
  server_version bigint NOT NULL DEFAULT 1
  last_mutation_id uuid NOT NULL
  saved_at timestamptz NOT NULL
  PRIMARY KEY(attempt_id, position)
  UNIQUE(attempt_id, last_mutation_id)

attempt_item_results
  attempt_id uuid FK attempts ON DELETE RESTRICT
  position integer NOT NULL
  problem_revision_id uuid FK problem_revisions ON DELETE RESTRICT
  verdict text CHECK ('correct','incorrect','partial','ungraded')
  earned_points numeric(7,2) NOT NULL
  grader_version text NOT NULL
  graded_detail jsonb NOT NULL DEFAULT '{}'
  PRIMARY KEY(attempt_id, position)
```

Autosave 요청에는 `last_mutation_id`와 현재 `server_version`을 보낸다. 서버는 한 statement로 UPSERT하되 기존 row 수정은 version 일치 때만 허용한다. 중복 mutation은 이전 성공을 반환하고, 다른 tab/device가 먼저 수정했으면 `409 Conflict`와 최신 row를 반환한다. 브라우저 시계의 timestamp로 승자를 정하지 않는다.

### 5.3 최초 정답·XP·티어·랭킹

```text
problem_solves
  id uuid PK
  user_id uuid FK users ON DELETE RESTRICT
  problem_id uuid FK problems ON DELETE RESTRICT
  first_correct_attempt_id uuid FK attempts ON DELETE RESTRICT
  first_correct_revision_id uuid FK problem_revisions ON DELETE RESTRICT
  solved_at timestamptz NOT NULL
  UNIQUE(user_id, problem_id)

xp_ledger
  id uuid PK
  user_id uuid FK users ON DELETE RESTRICT
  source_type text CHECK ('first_solve','admin_adjustment','event')
  source_key text NOT NULL
  delta bigint NOT NULL
  reason jsonb NOT NULL DEFAULT '{}'
  created_at timestamptz NOT NULL
  UNIQUE(user_id, source_type, source_key)

user_stats
  user_id uuid PK FK users ON DELETE CASCADE
  xp_total bigint NOT NULL DEFAULT 0
  solved_count bigint NOT NULL DEFAULT 0
  rating numeric(10,2) NOT NULL DEFAULT 0
  updated_at timestamptz NOT NULL

tier_rules
  tier_code text PK
  min_xp bigint UNIQUE NOT NULL
  label text NOT NULL
  icon_asset_key text NOT NULL
  sort_order integer UNIQUE NOT NULL

rating_events
  id uuid PK
  user_id uuid FK users ON DELETE RESTRICT
  attempt_id uuid FK attempts ON DELETE RESTRICT
  old_rating, new_rating numeric(10,2) NOT NULL
  algorithm_version text NOT NULL
  detail jsonb NOT NULL
  created_at timestamptz NOT NULL
  UNIQUE(user_id, attempt_id, algorithm_version)
```

`xp_ledger`가 source of truth이고 `user_stats`는 같은 트랜잭션에서 갱신하는 cache다. nightly 검증 job이 `SUM(xp_ledger.delta)`와 `user_stats.xp_total`을 대조한다. 랭킹은 `user_stats(xp_total DESC, solved_count DESC, user_id)` 인덱스로 읽고 banned/admin은 제외한다. 티어 기준 변경은 과거 ledger를 변조하지 않고 `tier_rules`만 versioning한다.

### 5.4 운영 테이블·필수 인덱스

- `admin_audit_log(actor_user_id, action, entity_type, entity_id, before_json, after_json, created_at)` — append-only
- `schema_migrations` — Alembic revision 기록
- `import_batches(source_name, source_version, manifest_hash UNIQUE, status, detail, created_at)` — agent 증분 import 멱등성
- `outbox_events(id, event_type, aggregate_id, payload, created_at, delivered_at)` — 향후 알림/비동기 집계가 필요할 때만
- `attempts(user_id, started_at DESC)`, `attempts(test_version_id, submitted_at)`
- `attempt_answers(attempt_id, saved_at)`
- `problem_revisions(problem_id, revision_no DESC)`
- `xp_ledger(user_id, created_at DESC)`
- `user_stats(xp_total DESC, solved_count DESC, user_id)`
- 검색이 실제 필요해진 key에만 GIN `jsonb` 인덱스를 추가한다. 모든 JSON에 무작정 GIN을 걸지 않는다.

## 6. 트랜잭션 경계

### 6.1 문제 또는 시험 publish

한 트랜잭션에서:

1. parent `problems` 또는 `tests` row를 `FOR UPDATE`로 잠근다.
2. 다음 revision/version 번호를 만든다.
3. 문제 revision 또는 시험 item이 참조할 **정확한 `problem_revision_id`**를 저장한다.
4. canonical payload manifest의 content hash를 저장한다.
5. validation(배점 합계, 중복 position, 정답 schema, asset hash)을 통과하면 `published_at`을 기록한다.
6. commit 후 published revision/version은 UPDATE/DELETE하지 않는다. 수정은 새 revision/version이다.

DB trigger 또는 제한된 DB role로 published row 수정 방지를 이중화한다. 이미 시작한 attempt는 항상 시작 당시 `test_version_id`를 계속 본다.

### 6.2 autosave

한 짧은 UPSERT만 실행한다. attempt가 `in_progress`인지 확인하고 `(attempt_id, position)` row를 insert/update한다. optimistic version이 맞지 않으면 덮어쓰지 않는다. API 재시도는 mutation UUID로 멱등하다.

### 6.3 시험 제출 + first-solve XP

한 DB 트랜잭션에서:

1. `attempts` row를 `FOR UPDATE`.
2. 이미 같은 idempotency key로 제출됐으면 저장된 결과 반환.
3. `in_progress`가 아니면 중복 제출로 처리.
4. 고정된 test/problem revisions와 저장 답안을 읽어 채점.
5. `attempt_item_results`를 저장하고 attempt를 `submitted`로 전환.
6. 정답 item마다 `problem_solves ... ON CONFLICT DO NOTHING RETURNING` 실행.
7. 실제로 새 `problem_solves`가 반환된 item만 `xp_ledger`에 `UNIQUE(user_id,'first_solve',problem_id)`로 기록.
8. 그 delta만 `user_stats`에 반영하고 commit.

이 경계 덕분에 새로고침·네트워크 retry·동시 submit이 XP를 중복 지급하지 않는다. 기본 격리는 `READ COMMITTED` + 명시 row lock/UNIQUE로 충분하다. 복잡한 동시 관리자 조작이 추가될 때만 `SERIALIZABLE`과 transaction retry를 제한적으로 사용한다.

## 7. 백업·복구 결정

관리형 PITR만 믿지 않고 **provider-independent logical backup**을 함께 둔다.

### 초기/현재 규모

- Neon Free history는 확인 시점 6시간이므로 편의 기능으로만 본다.
- 매일 새벽 `pg_dump --format=custom --no-owner` 실행.
- DB provider와 다른 계정/서비스의 S3-compatible bucket(R2/S3 등)에 암호화 업로드.
- 일일 30개, 월말 12개를 보관하는 정책을 시작점으로 한다.
- dump manifest에 PostgreSQL major, Alembic revision, SHA-256, 생성 시각을 기록한다.
- assets는 DB dump에 포함되지 않으므로 object versioning 또는 별도 asset manifest backup을 둔다.

### 공개 사용자가 생기면

- Neon Launch + 7일 history와 spending limit를 켠다.
- nightly logical backup은 계속 유지한다. 같은 provider 내부 PITR은 운영 실수 복구용, off-site dump는 provider/account 삭제·잠금까지 대비한 DR용이다.
- 목표: 평시 `RPO <= 24h`, restore `RTO <= 4h`; 사용량이 커지거나 유료 데이터가 생기면 더 짧은 RPO를 재결정한다.
- 분기마다 빈 임시 DB에 실제 restore하고 schema revision, row count, FK validation, 샘플 로그인 hash verification, 시험 제출 재생을 확인한다.
- migration 전에는 Neon snapshot/branch + 수동 `pg_dump`를 모두 만든다.

PostgreSQL 공식 문서는 SQL dump, filesystem backup, continuous archiving의 세 접근을 구분한다. 관리형 Neon에서는 built-in history와 표준 `pg_dump/pg_restore`를 조합하는 것이 현재 규모에 가장 단순하다.

## 8. 기존 25문항 마이그레이션

### 단계 1 — 추출·manifest

현재 `index.html`의 25문항을 다음 immutable manifest로 변환한다.

```text
source_key, slug, curriculum_code, prompt, choices,
answer_key, solution, points, asset files + SHA-256
```

문항마다 stable `source_key`를 부여하고 import batch의 manifest hash를 고정한다. 수식/HTML/SVG를 sanitize하고 learner payload와 answer payload를 분리한다.

### 단계 2 — idempotent seed

1. DB schema를 Alembic으로 배포.
2. admin user와 tier rules 생성.
3. `source_key`/content hash 기준으로 25개 problem + revision 1을 UPSERT하되 published revision은 수정하지 않는다.
4. 기존 실전 25를 `test` + `test_version 1` + 25 item으로 생성.
5. 전체 배점, 정답, item 순서, content hash가 원본 manifest와 일치할 때만 publish.
6. 같은 manifest 재실행은 no-op여야 한다.

### 단계 3 — shadow 검증

- 기존 브라우저 채점과 새 API 채점을 모든 정답/대표 오답에 대해 비교.
- 문제 수, revision 수, test item 수, 배점 합, asset hash, answer schema를 자동 검사.
- 새 API에서 signup → login → attempt 생성 → autosave → refresh 복구 → submit → first XP 1회 → 재제출 XP 0회를 E2E로 검증.

### 단계 4 — cutover

- 정적 사이트는 콘텐츠를 DB에서 직접 읽지 않고 API를 호출한다.
- 프런트에는 DB credential이 없다.
- 정적 구버전 fallback 없이 중앙 서비스 한 경로만 운영한다.
- `localStorage`는 네트워크 장애 중 답안 복구 캐시로만 사용하며 PostgreSQL 응시 기록을 권위 있는 원본으로 유지한다.
- 문제 추가 agent는 raw production SQL 대신 검증 가능한 manifest → dry-run → admin preview → publish API를 사용한다.

## 9. 지금 규모와 3년 후 판단

### 지금: 동생 1명 중심 + 공개 가입

SQLite, D1, Turso도 처리량만 보면 충분하다. 그러나 처리량이 작은 것이 데이터 모델도 임시여야 한다는 뜻은 아니다. 처음부터 PostgreSQL을 쓰면 추가되는 운영 복잡도는 Neon이 대부분 흡수하고, 인증/불변 version/first-solve uniqueness를 DB 제약으로 명확하게 구현할 수 있다. Neon Free로 개발하되 공개 가입 직전 abuse limit와 off-site backup을 완료한다.

### 3년 후

예상되는 복잡도는 단순 사용자 수보다 다음에서 온다.

- autosave로 인한 잦은 쓰기와 attempt history 증가
- 문제 revision과 시험 version 누적
- 관리자 검색·감사·import batch
- first-solve/XP/rating 이벤트의 정확한 재계산
- 랭킹과 사용자 통계
- 여러 API instance 또는 background worker

PostgreSQL은 이 변화에 맞춰 connection pool, index, materialized view, read replica, partitioning을 단계적으로 추가할 수 있다. 처음부터 partitioning이나 event microservice는 만들지 않는다. 다음 신호가 있을 때만 확장한다.

- slow query log/`pg_stat_statements`에서 반복 병목 확인 → 인덱스/쿼리 수정
- event/answer 테이블이 수천만 row 수준에 접근하고 vacuum·retention이 문제가 됨 → 월별 partition/archival 검토
- leaderboard read가 write workload를 방해 → 비동기 snapshot/materialized leaderboard 검토
- asset가 DB 크기를 주도 → object storage 정책 강화
- Neon 비용/정책이 불리해짐 → 표준 `pg_dump` 또는 logical replication으로 다른 PostgreSQL 이전

즉, **현재에는 과도하지 않고 3년 후에도 갈아엎지 않아도 되는 최소 공통분모가 관리형 PostgreSQL**이다.

## 10. 구현 승인 조건

다음이 충족돼야 이 결정을 구현 완료로 본다.

- [ ] PostgreSQL schema migration이 빈 DB와 upgrade DB에서 모두 성공
- [ ] published problem revision/test version 변경·삭제가 차단됨
- [ ] Unicode ID edge case와 1자 password Argon2id 계약 테스트 통과
- [ ] password 원문·session 원문 token이 DB/log에 없음
- [ ] autosave retry는 멱등하고 stale update는 409
- [ ] 동시 submit에서도 first-solve XP가 정확히 1회
- [ ] learner API가 제출 전 answer key/solution을 노출하지 않음
- [ ] admin mutation이 audit log에 남음
- [ ] nightly off-site dump 생성과 별도 DB restore 실검증
- [ ] 25문항 manifest 재실행이 no-op이고 원본 채점과 일치
- [ ] API/프런트 CORS·cookie가 실제 배포 origin에서 브라우저 E2E 통과

## 11. 공식 근거

### DB 특성

- DuckDB concurrency: https://duckdb.org/docs/current/connect/concurrency.html
- DuckDB 설계 목표: https://duckdb.org/why_duckdb
- SQLite 적절한 용도/서버 DB 비교: https://www.sqlite.org/whentouse.html
- SQLite WAL과 one-writer/network filesystem 제약: https://www.sqlite.org/wal.html
- PostgreSQL MVCC: https://www.postgresql.org/docs/current/mvcc-intro.html
- PostgreSQL JSON/JSONB: https://www.postgresql.org/docs/current/datatype-json.html
- PostgreSQL backup/restore: https://www.postgresql.org/docs/current/backup.html

### 관리형 후보·가격·복구

- D1 limits: https://developers.cloudflare.com/d1/platform/limits/
- D1 pricing: https://developers.cloudflare.com/d1/platform/pricing/
- D1 Time Travel: https://developers.cloudflare.com/d1/reference/time-travel/
- D1 transaction batch: https://developers.cloudflare.com/d1/worker-api/d1-database/#batch
- Neon docs index: https://neon.com/docs/llms.txt
- Neon plans: https://neon.com/docs/introduction/plans.md
- Neon pricing: https://neon.com/pricing
- Neon backup overview: https://neon.com/docs/manage/backups.md
- Supabase pricing: https://supabase.com/pricing.md
- Supabase backup: https://supabase.com/docs/guides/platform/backups.md
- Turso pricing: https://turso.tech/pricing
- Turso docs index: https://docs.turso.tech/llms.txt

---

**최종 한 줄:** 운영 source of truth는 **Neon의 표준 PostgreSQL**, 인증은 **자체 Argon2id + opaque session**, 콘텐츠는 **불변 problem revision/test version**, XP는 **unique first-solve + append-only ledger**로 구현한다. D1은 비용 최우선의 2순위이며 DuckDB는 offline 분석에만 사용한다.
