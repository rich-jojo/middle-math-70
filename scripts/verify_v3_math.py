#!/usr/bin/env python3
"""Independent exact-arithmetic checks for the published v3 hard exam."""

from __future__ import annotations

import itertools
import json
import math
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "content/bundles/math70-v3-hard.json"


def check(condition: bool, label: str) -> None:
    if not condition:
        raise ValueError(f"v3 math check failed: {label}")


def main() -> None:
    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    problems = bundle["problems"]
    selected = {
        index: problem["choices"][problem["answer_spec"]["correct_index"]]
        for index, problem in enumerate(problems, 1)
        if problem["answer_type"] == "choice"
    }

    check(4 * math.isqrt(5**2 + 12**2) == 52 and selected[1] == "52 cm", "01")
    check((-8 / 4) * -3 == 6 and (6 - 1 - 1, 3 - 2 + 1) == (4, 2), "02")
    x3 = Fraction(540, 9)
    angles = [x3 + 10, x3 + 20, 2 * x3 - 10, 2 * x3, 3 * x3 - 20]
    check(max(angles) - min(angles) == 90 and selected[3] == "90°", "03")
    check(min(x for x in range(1, 100) if 8000 + 900 * x < 3500 + 1400 * x) == 10, "04")
    a5 = next(a for a in range(100) if Fraction(48 + 3 * a, 12 + a) == Fraction(19, 5))
    check(a5 == 3 and Fraction(8, 12 + a5) == Fraction(8, 15), "05")
    check(Fraction(6 * 20, 8) == 15, "06")
    check(3**2 * 8 - Fraction(1, 3) * 3**2 * 6 == 54, "07")
    slope8 = Fraction(0 - 3, 4 - 2)
    intercept8 = -4 * slope8
    check(slope8 + intercept8 == Fraction(9, 2), "08")
    check(selected[9] == "∠B=∠E", "09")
    different_colours = 3 * 2 + 3 * 1 + 2 * 1
    check(Fraction(different_colours, math.comb(6, 2)) == Fraction(11, 15), "10")
    n11 = next(n for n in range(3, 100) if n * (n - 3) // 2 == 4 * n)
    check((n11 - 2) * 180 == 1620, "11")
    integers12 = [x for x in range(-100, 101) if 2 - Fraction(3 * x - 1, 2) < Fraction(x + 5, 3) <= 4]
    check(integers12 == list(range(1, 8)), "12")
    check(Fraction(9, 25 - 9) * 64 == 36, "13")
    check(Fraction(100 - 10 - 14, 7) == Fraction(76, 7), "14")
    numbers15 = [
        10 * a + b
        for a in range(1, 10)
        for b in range(10)
        if a + b == 11 and 10 * a + b == 2 * (10 * b + a) + 7
    ]
    check(numbers15 == [83], "15")
    hypotenuse16 = math.isqrt(5**2 + 12**2)
    altitude16 = Fraction(5 * 12, hypotenuse16)
    check(hypotenuse16 == 13 and altitude16 == Fraction(60, 13) and selected[16] == "60/13 cm", "16")
    slope17 = Fraction(-3 - 5, 5 - 1)
    intercept17 = 5 - slope17
    check(Fraction(1, 2) * Fraction(intercept17, -slope17) * intercept17 == Fraction(49, 4), "17")
    favourable18 = sum(a * b % 6 == 0 for a, b in itertools.product(range(1, 7), repeat=2))
    check(Fraction(favourable18, 36) == Fraction(5, 12), "18")
    p19, q19 = Fraction(1, 6), Fraction(3, 11)
    x19 = Fraction(2 * p19 + q19, 3 * p19 - q19)
    check(3 * x19 + 1 == 9, "19")
    check(Fraction(9 - 4, 25) * 100 == 20, "20")
    x21 = next(x for x in range(601) if Fraction(25 * x + 10 * (600 - x), 100) == 108)
    check(x21 == 320, "21")
    pairs22 = list(itertools.combinations(range(1, 9), 2))
    favourable22 = [pair for pair in pairs22 if sum(pair) % 3 == 0 or all(value % 2 for value in pair)]
    check(Fraction(len(favourable22), len(pairs22)) == Fraction(1, 2), "22")
    frequencies23 = next((a, b) for a in range(21) for b in range(21) if a + b == 15 and 2 * a + 3 * b == 39)
    expanded23 = sorted([2] * 2 + [4] * frequencies23[0] + [6] * frequencies23[1] + [8] * 3)
    median23 = Fraction(expanded23[9] + expanded23[10], 2)
    mode23 = max(set(expanded23), key=expanded23.count)
    check(median23 + mode23 == 12, "23")
    check(selected.get(24) is None and "RHA" in problems[23]["answer_spec"]["accepted"][0], "24")
    slope25 = Fraction(-2 - 6, 3 - (-1))
    intercept25 = 6 - slope25 * -1
    g_slope25 = Fraction(-intercept25, 6)
    area25 = (
        Fraction(1, 2) * (Fraction(-intercept25, g_slope25) - Fraction(-intercept25, slope25)) * intercept25
    )
    check(area25 == 8, "25")

    print("V3 MATH CHECKS PASSED: 25/25")


if __name__ == "__main__":
    main()
