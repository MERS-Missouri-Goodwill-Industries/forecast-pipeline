"""Day-of-week planning math.

The whole system rests on one idea: every day gets a share of the WHOLE YEAR, computed
against a single annual denominator. That makes the seven weekday values identical in
every month and identical for every store -- an open Friday is worth the same slice of
the year in January as in July -- and makes the column total exactly 1.0.

Do not renormalize per month. That was an earlier design and it is incompatible with flat
weekday shares: if January loses a day and the rest of January rises to compensate, then
January's Fridays no longer equal July's.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Iterable

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
# Excel WEEKDAY() numbering, Sunday = 1.
WEEKDAY_NUMBER = {
    "Sunday": 1, "Monday": 2, "Tuesday": 3, "Wednesday": 4,
    "Thursday": 5, "Friday": 6, "Saturday": 7,
}

_SEED_PATH = Path(__file__).with_name("seed_data.json")


def load_seed() -> dict:
    with _SEED_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Rescale so the seven weekday weights total exactly 1.0."""
    total = sum(weights.get(d, 0.0) for d in WEEKDAYS)
    if total <= 0:
        return {d: 1 / 7 for d in WEEKDAYS}
    return {d: weights.get(d, 0.0) / total for d in WEEKDAYS}


def is_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


def weekday_counts(year: int) -> dict[str, int]:
    """How many times each weekday falls in the given calendar year (52 or 53)."""
    counts = {d: 0 for d in WEEKDAYS}
    start = dt.date(year, 1, 1)
    for i in range(366 if is_leap(year) else 365):
        counts[WEEKDAYS[(start + dt.timedelta(days=i)).weekday()]] += 1
    return counts


def easter(year: int) -> dt.date:
    """Anonymous Gregorian algorithm. Easter 2027 = 28 March."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def fourth_thursday_november(year: int) -> dt.date:
    d = dt.date(year, 11, 1)
    return d + dt.timedelta(days=(3 - d.weekday()) % 7 + 21)


def default_holidays(year: int) -> list[dict]:
    return [
        {"date": dt.date(year, 1, 1).isoformat(), "label": "New Year's Day"},
        {"date": easter(year).isoformat(), "label": "Easter"},
        {"date": dt.date(year, 7, 4).isoformat(), "label": "July 4th"},
        {"date": fourth_thursday_november(year).isoformat(), "label": "Thanksgiving"},
        {"date": dt.date(year, 12, 24).isoformat(), "label": "Christmas Eve"},
        {"date": dt.date(year, 12, 25).isoformat(), "label": "Christmas Day"},
    ]


def expand_closures(closures: Iterable[dict]) -> list[dict]:
    """Turn {start, end, label} ranges into one entry per calendar day."""
    out: list[dict] = []
    for c in closures:
        start = dt.date.fromisoformat(c["start"])
        end = dt.date.fromisoformat(c["end"])
        label = (c.get("label") or "").strip() or "Store Closure"
        day = start
        while day <= end:
            out.append({"date": day.isoformat(), "label": label})
            day += dt.timedelta(days=1)
    return out


def build_day_factors(year: int, weights: dict[str, float], holidays: list[dict]) -> list[dict]:
    """Return one record per calendar day.

    day_pct_of_annual -- 0 on a closed day, otherwise the weekday weight over the combined
    weight of every OPEN day. Sums to exactly 1.0 across the year.

    day_pct_of_month -- reference only, the same day against its own month. Nothing is
    priced off it.
    """
    norm = normalize_weights(weights)
    closed = {h["date"]: h["label"] for h in holidays}

    days: list[dict] = []
    start = dt.date(year, 1, 1)
    for i in range(366 if is_leap(year) else 365):
        d = start + dt.timedelta(days=i)
        iso = d.isoformat()
        weekday = WEEKDAYS[d.weekday()]
        days.append({
            "date": iso,
            "date_obj": d,
            "month": d.month,
            "month_name": MONTH_NAMES[d.month - 1],
            "weekday": weekday,
            "holiday_label": closed.get(iso, ""),
            "weight": norm[weekday],
        })

    open_weight = sum(x["weight"] for x in days if not x["holiday_label"])
    for x in days:
        x["day_pct_of_annual"] = 0.0 if (x["holiday_label"] or open_weight == 0) else x["weight"] / open_weight

    month_totals = [0.0] * 13
    for x in days:
        month_totals[x["month"]] += x["day_pct_of_annual"]
    for x in days:
        mt = month_totals[x["month"]]
        x["day_pct_of_month"] = 0.0 if mt == 0 else x["day_pct_of_annual"] / mt

    return days


def compute_forecasted_bases(stores: list[dict], recommended_plan: float) -> dict[str, float]:
    """Split the plan across stores in proportion to base sales.

    Computed inline -- there is no named reallocation factor anywhere. A new store has no
    history, so it inherits the average base of up to three same-region comparables. The
    result sums to recommended_plan exactly.
    """
    continuing = [s for s in stores if s["status"] == "Continuing"]

    raw: dict[str, float] = {}
    for s in stores:
        if s["status"] == "Continuing":
            raw[s["code"]] = float(s["base_sales"])
        elif s["status"] == "Closed":
            raw[s["code"]] = 0.0
        else:
            comps = [c for c in continuing if c["region"] == s["region"]][:3]
            raw[s["code"]] = (
                sum(float(c["base_sales"]) for c in comps) / len(comps) if comps else 0.0
            )

    total = sum(raw.values())
    if total <= 0:
        return {code: 0.0 for code in raw}
    return {code: v / total * recommended_plan for code, v in raw.items()}


def build_store_plan(plan_base: float, day_factors: list[dict]) -> dict:
    """Daily dollars = plan_base * day_pct_of_annual. Zero variance by construction."""
    monthly = [0.0] * 12
    daily = []
    for d in day_factors:
        amount = plan_base * d["day_pct_of_annual"]
        monthly[d["month"] - 1] += amount
        daily.append({"date": d["date"], "amount": amount})
    return {
        "plan_base": plan_base,
        "monthly": monthly,
        "daily": daily,
        "full_year_total": sum(x["amount"] for x in daily),
    }


def weekday_mix(day_factors: list[dict]) -> list[dict]:
    """The seven flat weekday shares, for the Weekday Mix reference table."""
    per: dict[str, float] = {}
    for d in day_factors:
        if not d["holiday_label"] and d["weekday"] not in per:
            per[d["weekday"]] = d["day_pct_of_annual"]
    total = sum(per.values())
    return [
        {
            "day": day,
            "weekday_number": WEEKDAY_NUMBER[day],
            "day_pct_of_annual": per.get(day, 0.0),
            "pct_of_week": (per.get(day, 0.0) / total) if total else 0.0,
        }
        for day in WEEKDAYS
    ]


def month_row_ranges(day_factors: list[dict], first_row: int = 9) -> list[tuple[int, int]]:
    """(start_row, end_row) per month within the store-tab daily block."""
    ranges: list[tuple[int, int]] = []
    cursor = first_row
    for m in range(1, 13):
        count = sum(1 for d in day_factors if d["month"] == m)
        ranges.append((cursor, cursor + count - 1))
        cursor += count
    return ranges