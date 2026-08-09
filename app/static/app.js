let csrfToken = '';
let me = null;
let currentAttempt = null;
let currentSeq = 1;
let pendingAnswers = {};
let pendingFlags = {};
let saveTimer = null;
let timerHandle = null;
let saveChain = Promise.resolve();
let currentView = 'dashboard';

const $ = (selector) => document.querySelector(selector);
const root = () => $('#appRoot');
const h = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[char]));
const status = (message, bad = false) => {
  const element = $('#authStatus') || $('#status');
  if (!element) return;
  element.textContent = message;
  element.className = bad ? 'error status' : 'status';
};

async function api(path, options = {}) {
  options.headers = { ...(options.headers || {}), 'Content-Type': 'application/json' };
  if (csrfToken && !['GET', 'HEAD'].includes(options.method || 'GET')) {
    options.headers['X-CSRF-Token'] = csrfToken;
  }
  const response = await fetch(path, options);
  if (response.status === 401) {
    location.href = '/';
    return null;
  }
  if (!response.ok) {
    let payload;
    try { payload = await response.json(); } catch { payload = { detail: '요청을 처리하지 못했습니다.' }; }
    throw new Error(payload.detail || '요청을 처리하지 못했습니다.');
  }
  if (response.status === 204) return {};
  return response.json();
}

async function bootAuth() {
  const form = $('#authForm');
  if (!form) return;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    submitAuth('/api/login');
  });
  $('[data-signup]').addEventListener('click', () => submitAuth('/api/signup'));
}

async function submitAuth(path) {
  try {
    const form = $('#authForm');
    status('계정을 확인하는 중입니다.');
    const result = await api(path, {
      method: 'POST',
      body: JSON.stringify({ username: form.username.value, password: form.password.value })
    });
    csrfToken = result.csrf_token;
    location.href = '/app';
  } catch (error) {
    status(error.message, true);
  }
}

function setActiveView(view) {
  currentView = view;
  document.querySelectorAll('[data-view]').forEach((button) => {
    if (button.dataset.view === view) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
}

async function bootApp() {
  if (!root()) return;
  try {
    const result = await api('/api/me');
    me = result;
    csrfToken = result.csrf_token;
    document.querySelectorAll('[data-admin-only]').forEach((element) => { element.hidden = !me.is_admin; });
    document.querySelectorAll('[data-view]').forEach((button) => {
      button.onclick = () => render(button.dataset.view);
    });
    $('#logoutBtn').onclick = logout;
    const attemptId = location.hash.startsWith('#attempt-') ? location.hash.slice(9) : '';
    if (attemptId) {
      await loadAttempt(attemptId);
      return;
    }
    await render('dashboard');
  } catch {
    root().innerHTML = '<section class="panel"><h1>로그인이 필요합니다</h1><p>학습 기록을 열려면 다시 로그인하세요.</p><a href="/">로그인 화면으로</a></section>';
  }
}

async function logout() {
  await api('/api/logout', { method: 'POST' });
  location.href = '/';
}

async function render(view) {
  clearInterval(timerHandle);
  currentAttempt = null;
  setActiveView(view);
  root().innerHTML = '<div class="loading" role="status"><span></span><p>화면을 준비하는 중</p></div>';
  if (view === 'dashboard') return dashboard();
  if (view === 'problems') return problemList();
  if (view === 'exams') return examList();
  if (view === 'profile') return profileView();
  if (view === 'leaderboard') return leaderboard();
  if (view === 'admin') return adminView();
}

async function dashboard() {
  const profile = await api('/api/profile');
  const recent = profile.attempts[0];
  const recentScore = recent?.score == null ? '진행 중' : `${recent.score}점`;
  root().innerHTML = `
    <div class="page-head"><div><h1>오늘의 학습실</h1></div><p>풀이는 짧게 시작하고, 기록은 자동으로 남깁니다.</p></div>
    <section class="dashboard-grid">
      <article class="panel identity-panel">
        <div><p class="muted">${h(profile.user.tier.label_ko)}</p><h1>${h(profile.user.username)}</h1></div>
        <div class="stat-row">
          <div><strong>${profile.user.total_xp}</strong><span>누적 XP</span></div>
          <div><strong>${profile.solve_count}</strong><span>푼 문제</span></div>
          <div><strong>${profile.attempts.length}</strong><span>응시 기록</span></div>
        </div>
      </article>
      <article class="panel recent-panel">
        <div><p class="muted">최근 응시</p>${recent ? `<h2>${h(recent.title)}</h2><p>${h(recentScore)}</p>` : '<h2>첫 모의고사를 시작하세요</h2><p class="empty">아직 응시 기록이 없습니다.</p>'}</div>
        <button class="primary" ${recent ? `data-resume="${h(recent.id)}"` : 'data-open-exams'}>${recent ? '이어 풀기' : '모의고사 보기'}</button>
      </article>
    </section><p id="status" class="status" aria-live="polite"></p>`;
  $('[data-resume]')?.addEventListener('click', (event) => loadAttempt(event.currentTarget.dataset.resume));
  $('[data-open-exams]')?.addEventListener('click', () => render('exams'));
}

function problemQuery() {
  const params = new URLSearchParams();
  ['grade', 'semester', 'unit', 'level', 'status'].forEach((key) => {
    const value = $(`#f_${key}`)?.value;
    if (value) params.set(key, value);
  });
  return params.toString() ? `/api/problems?${params}` : '/api/problems';
}

async function problemList() {
  const result = await api(problemQuery());
  const problems = result.problems;
  const options = (key) => [...new Set(problems.map((problem) => problem[key]).filter(Boolean))]
    .map((value) => `<option>${h(value)}</option>`).join('');
  root().innerHTML = `
    <div class="page-head"><div><h1>문제은행</h1></div><p>${problems.length}개 문제에서 지금 필요한 단원을 골라 푸세요.</p></div>
    <section class="panel filters" aria-label="문제 필터">
      <label>학년<select id="f_grade"><option value="">전체</option>${options('grade')}</select></label>
      <label>학기<select id="f_semester"><option value="">전체</option>${options('semester')}</select></label>
      <label>단원<input id="f_unit" placeholder="단원 이름"></label>
      <label>레벨<input id="f_level" type="number" min="1" max="30" placeholder="1-30"></label>
      <label>풀이 상태<select id="f_status"><option value="">전체</option><option value="solved">해결</option><option value="unsolved">미해결</option></select></label>
      <button id="applyFilters">필터 적용</button>
    </section>
    <div class="problem-grid">${problems.map((problem) => `
      <button class="problem-card" data-p="${h(problem.id)}">
        <span class="badge">${problem.tier_badge_svg}</span>
        <span><strong>${h(problem.title)}</strong><span class="meta">${h(problem.unit)} / ${h(problem.tier.label_ko)} / ${problem.base_xp} XP</span></span>
        <span class="solve-state ${problem.solved ? '' : 'unsolved'}">${problem.solved ? '해결' : '미해결'}</span>
      </button>`).join('')}</div>
    ${problems.length ? '' : '<section class="panel"><h2>조건에 맞는 문제가 없습니다</h2><p>필터를 줄여 다시 찾아보세요.</p></section>'}
    <p id="status" class="status" aria-live="polite"></p>`;
  $('#applyFilters').onclick = problemList;
  document.querySelectorAll('[data-p]').forEach((button) => {
    button.onclick = () => problemDetail(button.dataset.p);
  });
}

function choiceMarkup(choices, selected = null) {
  return choices.map((choice) => `
    <button type="button" class="choice ${selected === choice.index ? 'selected' : ''}" data-choice="${choice.index}" aria-pressed="${selected === choice.index}">
      <span class="choice-index">${choice.index + 1}</span><span>${h(choice.text)}</span>
    </button>`).join('');
}

async function problemDetail(id) {
  const problem = await api(`/api/problems/${id}`);
  const answerControl = problem.answer_type === 'process'
    ? '<label class="answer-label">풀이 과정<textarea id="textAnswer" rows="7" placeholder="식을 세운 과정과 답을 함께 적으세요"></textarea></label>'
    : problem.answer_type === 'text'
      ? '<label class="answer-label">답<input id="textAnswer" placeholder="답을 입력하세요"></label>' : '';
  root().innerHTML = `
    <button class="back-button" data-back-problems>← 문제은행</button>
    <article class="question">
      <header class="question-head"><p>${h(problem.tier.label_ko)} / ${problem.base_xp} XP</p><h1>${h(problem.title)}</h1></header>
      ${problem.diagram_svg}<div class="question-body">${problem.body_html}</div>
      <div class="choices">${choiceMarkup(problem.choices)}</div>${answerControl}
      <div class="actions"><button class="primary" id="submitPractice">채점하기</button></div><div id="result" aria-live="polite"></div>
    </article>`;
  $('[data-back-problems]').onclick = () => problemList();
  document.querySelectorAll('[data-choice]').forEach((button) => {
    button.onclick = () => {
      document.querySelectorAll('.choice').forEach((item) => {
        item.classList.remove('selected');
        item.setAttribute('aria-pressed', 'false');
      });
      button.classList.add('selected');
      button.setAttribute('aria-pressed', 'true');
    };
  });
  $('#submitPractice').onclick = async () => {
    const selected = $('.choice.selected');
    const answer = problem.answer_type === 'choice'
      ? { choice: Number(selected?.dataset.choice ?? -1) }
      : { text: $('#textAnswer').value };
    const result = await api(`/api/problems/${id}/submit`, {
      method: 'POST',
      body: JSON.stringify({ answer, idempotency_key: crypto.randomUUID() })
    });
    $('#result').className = `result-box ${result.correct ? '' : 'bad'}`;
    $('#result').innerHTML = `<h2>${result.correct ? '정답입니다' : '다시 확인하세요'}</h2><p>${result.xp_awarded} XP 획득</p>${result.explanation_html}`;
    me = await api('/api/me');
  };
}

async function examList() {
  const result = await api('/api/exams');
  root().innerHTML = `
    <div class="page-head"><div><h1>모의고사</h1></div><p>답을 고르는 순간 자동 저장됩니다. 마지막에는 제출 검토를 거칩니다.</p></div>
    <div class="list">${result.exams.map((exam) => `
      <section class="panel list-row"><div><p class="muted">25문항 / 100점 / ${Math.round(exam.time_limit_seconds / 60)}분</p><h2>${h(exam.title)}</h2></div>
      <button class="primary" data-exam="${h(exam.slug)}">새로 시작</button></section>`).join('')}</div>`;
  document.querySelectorAll('[data-exam]').forEach((button) => {
    button.onclick = () => startExam(button.dataset.exam);
  });
}

async function startExam(slug) {
  const result = await api(`/api/exams/${slug}/attempts`, { method: 'POST', body: '{}' });
  location.hash = `attempt-${result.attempt_id}`;
  await loadAttempt(result.attempt_id);
}

function recoveryKey() {
  return currentAttempt ? `mm70-attempt-${currentAttempt.id}` : '';
}

function persistRecovery() {
  if (!currentAttempt) return;
  localStorage.setItem(recoveryKey(), JSON.stringify({ answers: pendingAnswers, flags: pendingFlags, currentSeq }));
}

function restoreRecovery() {
  try {
    const cached = JSON.parse(localStorage.getItem(recoveryKey()) || '{}');
    pendingAnswers = { ...(cached.answers || {}), ...pendingAnswers };
    pendingFlags = { ...(cached.flags || {}), ...pendingFlags };
    if (currentAttempt.items.some((item) => item.sequence === Number(cached.currentSeq))) currentSeq = Number(cached.currentSeq);
  } catch { /* Invalid recovery data is ignored. */ }
}

async function loadAttempt(id) {
  currentAttempt = await api(`/api/attempts/${id}`);
  pendingAnswers = { ...(currentAttempt.answers || {}) };
  pendingFlags = { ...(currentAttempt.flags || {}) };
  currentSeq = Number(currentAttempt.items?.[0]?.sequence || 1);
  restoreRecovery();
  setActiveView('exams');
  renderAttempt();
}

function answerFor(item) {
  return pendingAnswers[String(item.sequence)] || {};
}

function hasAnswer(item) {
  const answer = answerFor(item);
  return item.answer_type === 'choice' ? Number.isInteger(answer.choice) : Boolean(String(answer.text || '').trim());
}

function writeCurrent(item) {
  if (!item) return;
  const selected = $('.choice.selected');
  if (item.answer_type === 'choice') {
    if (selected) pendingAnswers[String(item.sequence)] = { choice: Number(selected.dataset.choice) };
  } else {
    pendingAnswers[String(item.sequence)] = { text: $('#examText')?.value || '' };
  }
  persistRecovery();
}

function setSaveStatus(message, state = '') {
  const element = $('#saveStatus');
  if (!element) return;
  element.textContent = message;
  element.className = `save-indicator ${state}`.trim();
}

function captureSave(item) {
  writeCurrent(item);
  return {
    sequence: String(item.sequence),
    answer: { ...(pendingAnswers[String(item.sequence)] || {}) },
    flagged: Boolean(pendingFlags[String(item.sequence)])
  };
}

function saveServer(item, announce = true) {
  if (!currentAttempt || currentAttempt.status !== 'in_progress') return saveChain;
  const attemptId = currentAttempt.id;
  const captured = captureSave(item);
  if (announce) setSaveStatus('자동 저장 중', 'saving');
  saveChain = saveChain.catch(() => {}).then(async () => {
    await api(`/api/attempts/${attemptId}/answers`, {
      method: 'PATCH',
      body: JSON.stringify({
        answers: { [captured.sequence]: captured.answer },
        flags: { [captured.sequence]: captured.flagged }
      })
    });
    if (announce && currentAttempt?.id === attemptId) setSaveStatus('자동 저장 완료');
  }).catch((error) => {
    if (currentAttempt?.id === attemptId) setSaveStatus(`저장 실패: ${error.message}`, 'error');
    throw error;
  });
  return saveChain;
}

function debounceSave(item) {
  setSaveStatus('자동 저장 대기', 'saving');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveServer(item), 320);
}

function navigateAttempt(item, sequence) {
  clearTimeout(saveTimer);
  saveServer(item, false);
  currentSeq = Number(sequence);
  persistRecovery();
  renderAttempt();
}

function renderAttempt() {
  clearInterval(timerHandle);
  const item = currentAttempt.items.find((candidate) => candidate.sequence === currentSeq);
  const answer = answerFor(item);
  const answeredCount = currentAttempt.items.filter(hasAnswer).length;
  const answerControl = item.answer_type === 'process'
    ? `<label class="answer-label">풀이 과정<textarea id="examText" rows="7" placeholder="식과 판단 근거를 순서대로 적으세요">${h(answer.text || '')}</textarea></label>`
    : item.answer_type === 'text'
      ? `<label class="answer-label">답<input id="examText" value="${h(answer.text || '')}" placeholder="답을 입력하세요"></label>` : '';
  root().innerHTML = `
    <div class="exam-page">
      <header class="exam-titlebar"><div><h1>${h(currentAttempt.title)}</h1><p>${answeredCount} / ${currentAttempt.items.length}문항 응답</p></div><p>답안은 입력 즉시 저장됩니다.</p></header>
      <div class="workstation">
        <aside class="panel exam-tools" aria-label="시험 도구">
          <div class="timer-block"><span>남은 시간</span><p id="timer">--:--</p></div>
          <p id="saveStatus" class="save-indicator" role="status" aria-live="polite">자동 저장 준비됨</p>
          <details class="palette" ${window.matchMedia('(min-width:721px)').matches ? 'open' : ''}><summary>문항표</summary>
            <div class="question-map">${currentAttempt.items.map((candidate) => {
              const classes = [hasAnswer(candidate) ? 'answered' : '', pendingFlags[String(candidate.sequence)] ? 'flagged' : '', candidate.sequence === currentSeq ? 'current' : ''].filter(Boolean).join(' ');
              return `<button data-jump="${candidate.sequence}" class="${classes}" aria-label="${candidate.sequence}번${hasAnswer(candidate) ? ', 응답함' : ', 미응답'}">${candidate.sequence}</button>`;
            }).join('')}</div>
          </details>
          <button id="submitExam" class="primary">제출 검토</button>
        </aside>
        <article class="question exam-question">
          <div class="question-progress"><strong>${currentSeq}번</strong><span>${item.points}점 / ${h(item.unit || '')}</span></div>
          <header class="question-head"><h2>${h(item.title)}</h2></header>
          ${item.diagram_svg}<div class="question-body">${item.body_html}</div>
          <div class="choices">${choiceMarkup(item.choices, answer.choice)}</div>${answerControl}
          <label class="flag"><input id="flagBox" type="checkbox" ${pendingFlags[String(item.sequence)] ? 'checked' : ''}> 나중에 다시 볼 문제로 표시</label>
          <div class="exam-nav"><button id="prev" ${currentSeq === currentAttempt.items[0].sequence ? 'disabled' : ''}>이전</button><span class="key-hint">← → 키로 이동</span><button id="next">${currentSeq === currentAttempt.items.at(-1).sequence ? '제출 검토' : '다음'}</button></div>
        </article>
      </div>
      <section id="submitReview" hidden></section>
    </div>`;
  document.querySelectorAll('[data-jump]').forEach((button) => {
    button.onclick = () => navigateAttempt(item, button.dataset.jump);
  });
  document.querySelectorAll('[data-choice]').forEach((button) => {
    button.onclick = () => {
      document.querySelectorAll('.choice').forEach((choice) => {
        choice.classList.remove('selected');
        choice.setAttribute('aria-pressed', 'false');
      });
      button.classList.add('selected');
      button.setAttribute('aria-pressed', 'true');
      writeCurrent(item);
      saveServer(item);
      refreshQuestionMapState(item);
    };
  });
  const answerInput = $('#examText');
  if (answerInput) {
    answerInput.addEventListener('input', () => {
      writeCurrent(item);
      debounceSave(item);
      refreshQuestionMapState(item);
    });
    answerInput.addEventListener('blur', () => {
      clearTimeout(saveTimer);
      saveServer(item);
    });
  }
  $('#flagBox').onchange = (event) => {
    pendingFlags[String(item.sequence)] = event.target.checked;
    persistRecovery();
    saveServer(item);
    refreshQuestionMapState(item);
  };
  $('#prev').onclick = () => {
    const index = currentAttempt.items.findIndex((candidate) => candidate.sequence === currentSeq);
    if (index > 0) navigateAttempt(item, currentAttempt.items[index - 1].sequence);
  };
  $('#next').onclick = () => {
    const index = currentAttempt.items.findIndex((candidate) => candidate.sequence === currentSeq);
    if (index === currentAttempt.items.length - 1) openSubmitReview(item);
    else navigateAttempt(item, currentAttempt.items[index + 1].sequence);
  };
  $('#submitExam').onclick = () => openSubmitReview(item);
  startTimer();
}

function refreshQuestionMapState(item) {
  const button = $(`[data-jump="${item.sequence}"]`);
  if (!button) return;
  button.classList.toggle('answered', hasAnswer(item));
  button.classList.toggle('flagged', Boolean(pendingFlags[String(item.sequence)]));
}

function openSubmitReview(item) {
  clearTimeout(saveTimer);
  saveServer(item, false);
  const unanswered = currentAttempt.items.filter((candidate) => !hasAnswer(candidate));
  const flagged = currentAttempt.items.filter((candidate) => pendingFlags[String(candidate.sequence)]);
  const box = $('#submitReview');
  box.hidden = false;
  box.innerHTML = `<div class="review-dialog" role="dialog" aria-modal="true" aria-labelledby="reviewTitle">
    <h2 id="reviewTitle">제출 전 마지막 확인</h2><p>최종 제출 후에는 답안을 바꿀 수 없습니다.</p>
    <div class="review-summary"><div><strong>${unanswered.length}</strong><span>미응답</span></div><div><strong>${flagged.length}</strong><span>다시 볼 문제</span></div></div>
    <p class="review-numbers">미응답: ${h(unanswered.map((candidate) => candidate.sequence).join(', ') || '없음')}</p>
    <p class="review-numbers">다시 볼 문제: ${h(flagged.map((candidate) => candidate.sequence).join(', ') || '없음')}</p>
    <div class="actions"><button data-return>시험으로 돌아가기</button><button class="primary" data-final-submit>최종 제출</button></div>
  </div>`;
  $('[data-return]').onclick = () => { box.hidden = true; };
  $('[data-final-submit]').onclick = submitExam;
  $('[data-return]').focus();
}

async function submitExam() {
  try {
    clearTimeout(saveTimer);
    await saveChain.catch(() => {});
    const result = await api(`/api/attempts/${currentAttempt.id}/submit`, {
      method: 'POST',
      body: JSON.stringify({ answers: pendingAnswers, idempotency_key: crypto.randomUUID() })
    });
    clearInterval(timerHandle);
    localStorage.removeItem(recoveryKey());
    location.hash = '';
    currentAttempt = null;
    root().innerHTML = `<section class="result-hero"><div class="result-score">${result.score}</div><div><h1>채점이 끝났습니다</h1><p>${result.xp_awarded} XP를 획득했습니다.</p></div></section>
      <section class="panel review-list"><h2>문항별 해설</h2>${result.review.map((entry) => `<details><summary>${entry.sequence}번 / ${entry.points}점 / ${entry.correct ? '정답' : '오답'}</summary>${entry.explanation_html}</details>`).join('')}</section>`;
  } catch (error) {
    setSaveStatus(error.message, 'error');
  }
}

function startTimer() {
  const tick = () => {
    const element = $('#timer');
    if (!element) return;
    const seconds = Math.max(0, Math.floor((new Date(currentAttempt.deadline_at) - new Date()) / 1000));
    element.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    if (seconds <= 0) {
      setSaveStatus('응시 시간이 종료되었습니다.', 'error');
      clearInterval(timerHandle);
    }
  };
  tick();
  timerHandle = setInterval(tick, 1000);
}

async function profileView() {
  const profile = await api('/api/profile');
  root().innerHTML = `<div class="page-head"><div><h1>학습 기록</h1></div><p>점수보다 풀이가 쌓이는 흐름을 확인하세요.</p></div>
    <section class="dashboard-grid"><article class="panel"><p class="muted">${h(profile.user.tier.label_ko)}</p><h2>${h(profile.user.username)}</h2><div class="stat-row"><div><strong>${profile.user.total_xp}</strong><span class="muted">XP</span></div><div><strong>${profile.solve_count}</strong><span class="muted">푼 문제</span></div></div></article>
    <article class="panel"><h2>응시 기록</h2><div class="list">${profile.attempts.map((attempt) => `<button class="list-row" data-resume="${h(attempt.id)}"><span>${h(attempt.title)}</span><strong>${attempt.score == null ? '진행 중' : `${attempt.score}점`}</strong></button>`).join('') || '<p class="muted">아직 응시 기록이 없습니다.</p>'}</div></article></section>`;
  document.querySelectorAll('[data-resume]').forEach((button) => {
    button.onclick = () => loadAttempt(button.dataset.resume);
  });
}

async function leaderboard() {
  const result = await api('/api/leaderboard');
  root().innerHTML = `<div class="page-head"><div><h1>학습 순위</h1></div><p>첫 풀이 XP를 기준으로 정렬됩니다.</p></div>
    <table><thead><tr><th>순위</th><th>사용자</th><th>XP</th><th>티어</th></tr></thead><tbody>${result.users.map((user, index) => `<tr><td>${index + 1}</td><td>${h(user.username)}</td><td>${user.total_xp}</td><td>${h(user.tier.label_ko)}</td></tr>`).join('')}</tbody></table>`;
}

async function adminView() {
  try {
    const [problemResult, examResult] = await Promise.all([api('/api/admin/problems'), api('/api/admin/exams')]);
    root().innerHTML = `<div class="page-head"><div><h1>콘텐츠 관리</h1></div><p>게시된 신버전 문제와 시험을 확인합니다.</p></div>
      <section class="dashboard-grid"><article class="panel"><h2>문제</h2><p>${problemResult.problems.length}개 / 초안 포함</p><div class="list">${problemResult.problems.slice(0, 20).map((problem) => `<span>${h(problem.external_key)} / ${h(problem.title)} / ${h(problem.state)}</span>`).join('')}</div></article>
      <article class="panel"><h2>시험</h2><p>${examResult.exams.length}개 시험</p></article></section>
      <section class="panel"><h2>번들 가져오기</h2><label>파일 경로<input id="bundlePath" value="content/bundles/math70-v3-hard.json"></label><div class="actions"><button id="dry">검증</button><button id="import" class="primary">가져오기</button></div><pre id="adminOut"></pre></section>`;
    $('#dry').onclick = () => runImport(true);
    $('#import').onclick = () => runImport(false);
  } catch (error) {
    root().innerHTML = `<section class="panel"><h1>관리 화면을 열 수 없습니다</h1><p class="error">${h(error.message)}</p></section>`;
  }
}

async function runImport(dryRun) {
  const result = await api('/api/admin/import', {
    method: 'POST', body: JSON.stringify({ path: $('#bundlePath').value, dry_run: dryRun })
  });
  $('#adminOut').textContent = JSON.stringify(result, null, 2);
}

document.addEventListener('keydown', (event) => {
  if (!currentAttempt?.items) return;
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
  if (event.key === 'ArrowRight') $('#next')?.click();
  if (event.key === 'ArrowLeft') $('#prev')?.click();
  if (event.key.toLowerCase() === 'f') {
    const flag = $('#flagBox');
    if (flag) {
      flag.checked = !flag.checked;
      flag.dispatchEvent(new Event('change'));
    }
  }
});

bootAuth();
bootApp();
