#!/usr/bin/env python3
"""중등 수학 70점 돌파 v2 단일 HTML 앱 브라우저 회귀 테스트."""

from __future__ import annotations

import http.server
import os
import socketserver
import threading
import time
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "legacy.html"
ARTIFACTS = ROOT / "test-artifacts"
PORT = 8876


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS  {message}")


def js(driver, source):
    return driver.execute_script(source)


def click(driver, selector, by=By.CSS_SELECTOR):
    element = driver.find_element(by, selector)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", element)
    return element


def new_driver(width=1440, height=1000):
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--force-device-scale-factor=1")
    opts.add_argument(f"--window-size={width},{height}")
    opts.add_argument("--lang=ko-KR")
    driver = webdriver.Chrome(options=opts)
    driver.set_window_size(width, height)
    return driver


def wait_ready(driver):
    WebDriverWait(driver, 10).until(lambda d: js(d, "return document.readyState") == "complete")
    WebDriverWait(driver, 10).until(
        lambda d: js(d, "return typeof startMock==='function' && typeof mock!=='undefined'")
    )


def data_contract_test(driver):
    data = js(
        driver,
        "return mock.map(q=>({unit:q.unit,type:q.type||'choice',answer:q.answer,options:q.options||[],diagram:q.diagram||'',rubric:q.rubric||'',steps:q.steps||[],trap:q.trap||'',retry:q.retry||''}))",
    )
    check(len(data) == 25, "실전 문항 수 25")
    types = [q["type"] for q in data]
    check(types[:20] == ["choice"] * 20, "1~20번은 객관식")
    check(types[20:23] == ["text"] * 3, "21~23번은 단답형")
    check(types[23:] == ["process"] * 2, "24~25번은 과정형")
    dist = [sum(q.get("answer") == i for q in data[:20]) for i in range(5)]
    check(dist == [4, 4, 4, 4, 4], f"객관식 정답 위치 균형 {dist}")
    basic_units = [sum(q["unit"].startswith(term) for q in data[:18]) for term in ("중1-2", "중2-1", "중2-2")]
    total_units = [sum(q["unit"].startswith(term) for q in data) for term in ("중1-2", "중2-1", "중2-2")]
    check(basic_units == [6, 6, 6], f"1~18 기본 72점의 학기별 6문항 {basic_units}")
    check(total_units == [7, 10, 8], f"전체 학기별 7·10·8문항 {total_units}")
    check(
        all(len(q["options"]) == 5 and len(set(q["options"])) == 5 for q in data[:20]),
        "객관식 보기 5개 및 중복 없음",
    )
    check(
        all(len(q["steps"]) >= 2 and q["trap"] and q["retry"] for q in data), "전 문항 해설·오답·재시험 기준"
    )
    check(all(q["rubric"] for q in data[23:]), "과정형 부분점수 루브릭")
    check(sum(bool(q["diagram"]) for q in data) >= 18, "실전 시각자료 18문항 이상")
    for n, q in enumerate(data, 1):
        if not q["diagram"]:
            continue
        soup = BeautifulSoup(q["diagram"], "html.parser")
        svgs = soup.find_all("svg")
        check(bool(svgs), f"{n}번 시각자료에 SVG 존재")
        check(
            all(s.get("role") == "img" and s.get("aria-label", "").strip() for s in svgs),
            f"{n}번 SVG role/한국어 aria-label",
        )
        check(
            all(any("가" <= c <= "힣" for c in s.get("aria-label", "")) for s in svgs),
            f"{n}번 SVG 한국어 설명",
        )


def interaction_test(driver, base):
    driver.get(base)
    wait_ready(driver)
    js(
        driver,
        "localStorage.clear(); mockAnswers=Array(25).fill(null); mockFlags=Array(25).fill(false); mockIndex=0; mockLeft=7200; mockSubmitted=false; startMock(); clearInterval(mockInterval)",
    )
    check(js(driver, "return mockIndex") == 0, "실전 1번 시작")
    js(driver, "showSolutions()")
    check(
        "실전 제출 후 공개" in driver.find_element(By.ID, "solutionList").text, "실전 제출 전 정오·해설 차단"
    )
    js(driver, "showView('mock'); renderMock()")
    click(driver, "mockNext", By.ID)
    check(
        js(driver, "return mockIndex") == 1 and js(driver, "return mockAnswers[0]") is None,
        "무응답 다음 이동",
    )
    click(driver, "mockPrev", By.ID)
    check(js(driver, "return mockIndex") == 0, "무응답 이전 이동")
    js(driver, "jumpMock(9)")
    check(js(driver, "return mockIndex") == 9, "번호 점프")
    click(driver, "flagBtn", By.ID)
    check(
        js(driver, "return mockFlags[9]") is True and "검토" in driver.find_element(By.ID, "flagBtn").text,
        "검토 표시/해제 상태",
    )
    js(driver, "jumpMock(0); answerMock(1); answerMock(2)")
    check(js(driver, "return mockAnswers[0]") == 2, "객관식 답 변경")
    js(driver, "jumpMock(20); answerMockText('1800')")
    check(js(driver, "return mockAnswers[20]") == "1800", "단답 답 변경 가능")
    js(driver, "jumpMock(24)")
    click(driver, "mockNext", By.ID)
    review = driver.find_element(By.ID, "submitReview")
    check(review.is_displayed(), "미응답·검토 제출 리뷰(비 native)")
    check("미응답" in review.text and "검토" in review.text, "제출 리뷰에 미응답·검토 집계")
    click(driver, "#submitReview [data-action='return']")
    check(not review.is_displayed(), "제출 리뷰에서 시험으로 복귀")

    # 정답 전체 입력 및 100점 채점
    js(
        driver,
        "mockAnswers=mock.map(q=>q.type==='choice'?q.answer:q.accept[0]); mockFlags=Array(25).fill(false); saveState(); openSubmitReview()",
    )
    click(driver, "#submitReview [data-action='submit']")
    check("100점" in driver.find_element(By.ID, "resultContent").text, "25문항 전부 정답 시 100점")
    check(js(driver, "return processPoints(mock[23],'AO=CO SAS',23)") == 2, "24번 루브릭 부분점수 계산")
    check(js(driver, "return processPoints(mock[24],'P=(3,4)',24)") == 2, "25번 루브릭 교점 2점 계산")

    # 학습 모드도 무응답 이동·번호 이동·명시적 건너뛰기를 보장한다.
    js(driver, "startWarmup(); clearInterval(warmInterval)")
    click(driver, "warmNext", By.ID)
    check(
        js(driver, "return warmIndex") == 1 and js(driver, "return warmAnswers[0]") is None,
        "학습 무응답 다음 이동",
    )
    click(driver, "warmPrev", By.ID)
    check(js(driver, "return warmIndex") == 0, "학습 무응답 이전 이동")
    js(driver, "jumpWarmup(7)")
    check(js(driver, "return warmIndex") == 7, "학습 번호 이동")
    click(driver, ".exam-actions .middle")
    check(js(driver, "return warmIndex") == 8, "학습 명시적 모르겠음·건너뛰기")


def persistence_test(driver, base):
    driver.get(base)
    wait_ready(driver)
    js(
        driver,
        "localStorage.clear(); startMock(); clearInterval(mockInterval); answerMock(1); jumpMock(4); toggleFlag(); mockLeft=7011; saveState()",
    )
    driver.refresh()
    wait_ready(driver)
    js(driver, "startMock(); clearInterval(mockInterval)")
    check(js(driver, "return mockAnswers[0]") == 1, "자동저장 답안 복원")
    check(js(driver, "return mockFlags[4]") is True, "자동저장 검토 상태 복원")
    check(js(driver, "return mockIndex") == 4, "자동저장 현재 문항 복원")
    check(js(driver, "return mockLeft") <= 7011, "타이머 저장/복원")


def keyboard_mobile_print_test(driver, base):
    driver.get(base)
    wait_ready(driver)
    js(
        driver,
        "localStorage.clear(); mockAnswers=Array(25).fill(null); mockFlags=Array(25).fill(false); mockIndex=0; mockLeft=7200; mockSubmitted=false; startMock(); clearInterval(mockInterval)",
    )
    driver.find_element(By.CSS_SELECTOR, "body").send_keys(Keys.ARROW_RIGHT)
    check(js(driver, "return mockIndex") == 1, "키보드 오른쪽 화살표 다음 이동")
    driver.find_element(By.CSS_SELECTOR, "body").send_keys("f")
    check(js(driver, "return mockFlags[1]") is True, "키보드 F 검토 표시")
    click(driver, "timerToggle", By.ID)
    check("숨김" in driver.find_element(By.ID, "mockTimer").text, "타이머 숨기기")

    driver.set_window_size(390, 844)
    time.sleep(0.2)
    check(driver.find_element(By.ID, "mobileMapToggle").is_displayed(), "모바일 문항표 버튼 표시")
    click(driver, "mobileMapToggle", By.ID)
    check(driver.find_element(By.ID, "questionMap").is_displayed(), "모바일 문항표 열기")
    click(driver, "mobileMapToggle", By.ID)
    check(not driver.find_element(By.ID, "questionMap").is_displayed(), "모바일 문항표 닫기")
    click(driver, "mobileMapToggle", By.ID)
    check(driver.find_element(By.ID, "questionMap").is_displayed(), "모바일 문항표 열기")
    close = driver.find_element(By.CSS_SELECTOR, "#mockSidebar .mobile-map-close")
    check(close.is_displayed(), "모바일 문항표 내부 닫기 버튼")
    click(driver, "#mockSidebar .mobile-map-close")
    check(not driver.find_element(By.ID, "mockSidebar").is_displayed(), "모바일 문항표 닫기 후 문제 복귀")
    check(
        js(driver, "return document.documentElement.scrollWidth <= document.documentElement.clientWidth"),
        "모바일 가로 스크롤 없음",
    )
    click(driver, "mobileMapToggle", By.ID)
    ARTIFACTS.mkdir(exist_ok=True)
    driver.save_screenshot(str(ARTIFACTS / "mobile-cbt.png"))

    driver.set_window_size(1440, 1000)
    driver.get(base + "?print=exam&test=1")
    wait_ready(driver)
    check(
        js(driver, "return document.querySelectorAll('#printRoot .print-question').length") == 25,
        "인쇄 문제지 25문항",
    )
    check(
        "객관식 1~20" in driver.find_element(By.ID, "printRoot").text
        and "과정형 24~25" in driver.find_element(By.ID, "printRoot").text,
        "인쇄 응답 유형 구분",
    )
    check(
        js(driver, "return document.querySelectorAll('#printRoot .process-space .write-line').length") >= 10,
        "과정형 문항당 풀이 공간 5줄 이상",
    )
    driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"media": "print"})
    driver.save_screenshot(str(ARTIFACTS / "print-exam.png"))

    driver.get(base + "?print=solutions&test=1")
    wait_ready(driver)
    check(
        js(driver, "return document.querySelectorAll('#printRoot .print-sol').length") == 25,
        "인쇄 해설 25문항",
    )
    check(
        js(driver, "return document.querySelectorAll('#printRoot .print-sol svg').length") >= 18,
        "인쇄 해설 시각자료 포함",
    )
    check("채점 루브릭" in driver.find_element(By.ID, "printRoot").text, "인쇄 과정형 채점 루브릭")
    driver.save_screenshot(str(ARTIFACTS / "print-solutions.png"))
    driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"media": "screen"})


def static_accessibility_test():
    soup = BeautifulSoup(HTML.read_text(encoding="utf-8"), "html.parser")
    check(soup.html.get("lang") == "ko", "문서 한국어 lang")
    check(bool(soup.select_one(".skip-link")), "본문 바로가기 링크")
    check(
        "window.alert" not in HTML.read_text(encoding="utf-8")
        and "confirm(" not in HTML.read_text(encoding="utf-8"),
        "native alert/confirm 미사용",
    )
    check(
        bool(soup.select_one("@media print"))
        if False
        else "@media print" in HTML.read_text(encoding="utf-8"),
        "인쇄 CSS 존재",
    )
    check(
        "filter:grayscale(1)" in HTML.read_text(encoding="utf-8")
        and "stroke-dasharray" in HTML.read_text(encoding="utf-8"),
        "흑백 인쇄 및 선종류 구분",
    )
    for name in ("middle-math-70-exam.pdf", "middle-math-70-solutions.pdf"):
        pdf = ROOT / name
        check(
            pdf.exists() and pdf.stat().st_size > 100_000 and pdf.read_bytes()[:5] == b"%PDF-",
            f"기존 PDF 파일명 계약 및 유효 PDF: {name}",
        )


def main():
    static_accessibility_test()
    os.chdir(ROOT)
    with ReusableTCPServer(("127.0.0.1", PORT), QuietHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{PORT}/legacy.html"
        driver = new_driver()
        try:
            driver.get(base)
            wait_ready(driver)
            data_contract_test(driver)
            interaction_test(driver, base)
            persistence_test(driver, base)
            keyboard_mobile_print_test(driver, base)
            driver.set_window_size(1440, 1000)
            driver.get(base)
            wait_ready(driver)
            js(
                driver,
                "localStorage.clear(); mockAnswers=Array(25).fill(null); mockFlags=Array(25).fill(false); mockIndex=0; mockLeft=7200; mockSubmitted=false; startMock(); clearInterval(mockInterval)",
            )
            driver.save_screenshot(str(ARTIFACTS / "desktop-cbt.png"))
        finally:
            driver.quit()
            server.shutdown()
    print("\nALL TESTS PASSED")
    print(f"Screenshots: {ARTIFACTS}")


if __name__ == "__main__":
    main()
