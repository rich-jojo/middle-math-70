# 중등 수학 70점 돌파 모의고사 v2

중1-2, 중2-1, 중2-2 범위의 기본 점수를 먼저 회수하도록 만든 **의존성 없는 단일 HTML CBT 앱**입니다. 모든 문항·보기·해설·SVG는 새로 작성했으며 특정 학원의 공식 자료가 아닙니다.

## v2 구성

- 실전 모의고사: **25문항 × 4점 = 100점**, 제한 시간 120분
- 응답 형식: **객관식 1~20 / 단답형 21~23 / 과정형 24~25**
- 기본 점수 은행: **1~18번 = 72점**
  - 중1-2, 중2-1, 중2-2에서 각각 기본 6문항
- 전체 범위 배분: 중1-2 7문항 / 중2-1 10문항 / 중2-2 8문항
- 객관식 정답 위치: ①~⑤가 각각 정확히 4회
- 실전 시각자료: 20문항에 풀이용 SVG 도형·표·그래프
- 해설: 풀이 단계, 대표 오답 기준, 수치가 바뀐 재시험, 과정형 부분점수 루브릭

문항 설계의 세부 근거는 `V2_ITEM_BLUEPRINT.md`를 참고하세요.

## CBT 기능

- 데스크톱 고정 문항 사이드바와 모바일 접이식 문항표
- 응답 / 미응답 / 검토 상태 표시와 번호 이동
- 무응답 이전·다음·건너뛰기, 답안 수정
- 답안·검토·남은 시간·현재 문항을 `localStorage`에 자동 저장하고 복원
- 타이머 숨기기, 45·75·100분 무음 체크포인트
- 키보드: `←` 이전, `→` 다음, `F` 검토 표시, 객관식 `1`~`5`
- 학습 모드의 명시적 `모르겠음 · 건너뛰기`
- 실전 제출 전 정오·해설 차단
- native `alert`/`confirm` 대신 미응답·검토 문항을 보여 주는 제출 리뷰
- 과정형 자동 부분점수와 제출 뒤 오답·부분점수 해설

## 접근성·인쇄

- 모든 SVG에 `role="img"`와 한국어 `aria-label` 적용
- 평행·직각·같은 길이·그래프 계열을 색뿐 아니라 점선·각/길이 표식·텍스트로 구분
- 인쇄 시 SVG 회색조 적용, A4 페이지 분리와 문항 내부 잘림 방지
- 문제지에서 객관식/단답형/과정형을 별도 구역으로 구분
- 과정형 문항마다 6줄의 풀이 공간 제공
- 인쇄 해설에 원문 시각자료와 과정형 채점 루브릭 포함

직접 인쇄 경로:

- 문제지: `index.html?print=exam`
- 해설지: `index.html?print=solutions`

기존 공개 파일명 계약은 그대로 유지합니다.

- `middle-math-70-exam.pdf` — A4 11쪽
- `middle-math-70-solutions.pdf` — A4 14쪽

PDF를 다시 만들 때는 Chrome 기본 머리말·URL·꼬리말이 들어가지 않도록 반드시 `--no-pdf-header-footer`를 사용합니다.

```bash
google-chrome --headless=new --no-sandbox --no-pdf-header-footer \
  --print-to-pdf=middle-math-70-exam.pdf \
  'http://127.0.0.1:8765/index.html?print=exam&test=1'
google-chrome --headless=new --no-sandbox --no-pdf-header-footer \
  --print-to-pdf=middle-math-70-solutions.pdf \
  'http://127.0.0.1:8765/index.html?print=solutions&test=1'
```

## 로컬 실행

정적 파일이므로 `index.html`을 직접 열 수 있습니다. 자동저장과 인쇄 팝업을 안정적으로 확인하려면 로컬 서버를 권장합니다.

```bash
python3 -m http.server 8765
```

그 뒤 `http://127.0.0.1:8765/index.html`을 엽니다. 외부 JavaScript, 웹 폰트, MathJax 또는 빌드 단계가 필요하지 않습니다.

## 브라우저 자동검사

`browser_test.py`는 실제 headless Chrome으로 다음을 검증합니다.

- 문항 수·응답 형식·학기 배분·1~18 기본 72점 구조
- 객관식 정답 분포·보기 중복
- 시각자료 수·SVG ARIA·흑백 선 구분
- 학습/실전 무응답 이동·번호 점프·검토·답변 변경
- 제출 전 해설 차단·제출 리뷰·과정형 부분점수·100점 채점
- 답안/검토/시간/현재 문항 저장 복원
- 키보드·타이머 숨김·390px 모바일 문항표·가로 스크롤
- 문제지/해설 인쇄 경로와 과정형 풀이 공간·루브릭
- 데스크톱·모바일·문제지·해설지 PNG 스크린샷

처음 한 번 테스트 환경을 만듭니다.

```bash
python3 -m venv .venv
.venv/bin/pip install selenium beautifulsoup4
```

검사 실행:

```bash
.venv/bin/python browser_test.py
```

성공 시 마지막에 `ALL TESTS PASSED`가 출력되고 스크린샷은 `test-artifacts/`에 생성됩니다.

## 파일

- `index.html`: v2 단일 HTML 웹 앱
- `V2_ITEM_BLUEPRINT.md`: 문항 청사진과 오답·채점 설계
- `browser_test.py`: Chrome 기반 정적·상호작용·인쇄 회귀 테스트
- `middle-math-70-exam.pdf`: 기존 계약 파일명의 문제지 PDF
- `middle-math-70-solutions.pdf`: 기존 계약 파일명의 해설지 PDF
