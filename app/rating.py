from __future__ import annotations

GROUPS = ["Bronze", "Silver", "Gold", "Platinum", "Diamond", "Ruby"]
SUBS = ["V", "IV", "III", "II", "I"]
KO_GROUPS = {
    "Bronze": "브론즈",
    "Silver": "실버",
    "Gold": "골드",
    "Platinum": "플래티넘",
    "Diamond": "다이아몬드",
    "Ruby": "루비",
    "Master": "마스터",
}


def tier_for_level(level: int) -> dict:
    level = max(1, min(30, int(level)))
    group = GROUPS[(level - 1) // 5]
    sub = SUBS[(level - 1) % 5]
    return {"level": level, "group": group, "sublevel": sub, "label_ko": f"{KO_GROUPS[group]} {sub}"}


def tier_for_xp(xp: int) -> dict:
    if xp >= 10_000:
        return {"level": 31, "group": "Master", "sublevel": "", "label_ko": "마스터"}
    level = min(30, max(1, xp // 100 + 1))
    return tier_for_level(level)


def tier_badge_svg(level: int, label: str | None = None) -> str:
    tier = tier_for_level(level)
    label = label or tier["label_ko"]
    palette = {
        "Bronze": ("#8b5e3c", "#f4d2a3"),
        "Silver": ("#667085", "#e5e7eb"),
        "Gold": ("#a16207", "#fde68a"),
        "Platinum": ("#0f766e", "#a7f3d0"),
        "Diamond": ("#2563eb", "#bfdbfe"),
        "Ruby": ("#be123c", "#fecdd3"),
    }
    dark, light = palette[tier["group"]]
    points = "50,5 90,28 90,72 50,95 10,72 10,28"
    return (
        f'<svg viewBox="0 0 100 100" role="img" aria-label="{label}" xmlns="http://www.w3.org/2000/svg">'
        f'<polygon points="{points}" fill="{light}" stroke="{dark}" stroke-width="6"/>'
        f'<circle cx="50" cy="50" r="22" fill="white" stroke="{dark}" stroke-width="4"/>'
        f'<text x="50" y="57" text-anchor="middle" font-size="20" font-family="sans-serif" fill="{dark}">{tier["sublevel"]}</text>'
        "</svg>"
    )
