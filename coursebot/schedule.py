import re
from datetime import date, datetime, timedelta

from .errors import DataError
from .storage import TZ


def normalize_courses(rows):
    result = []
    for row in rows:
        try:
            mask = str(row["SKZC"] or "")
            if not re.fullmatch(r"[01]{1,60}", mask):
                raise ValueError
            day, begin, end = int(row["SKXQ"]), int(row["KSJC"]), int(row["JSJC"])
            if not 1 <= day <= 7 or not 1 <= begin <= end <= 30 or not row["KCM"]:
                raise ValueError
            start_time = clock(row.get("KSSJ"))
            end_time = clock(row.get("JSSJ"))
            if bool(start_time) != bool(end_time) or (start_time and start_time >= end_time):
                raise ValueError
            kind = str(row.get("SFSYK", ""))
            if kind not in ("0", "1"):
                raise ValueError
            result.append({
                "name": str(row["KCM"]), "teacher": str(row.get("SKJS") or ""),
                "room": str(row.get("JASMC") or ""), "weekday": day,
                "begin": begin, "end": end, "weeks": [i + 1 for i, bit in enumerate(mask) if bit == "1"],
                "start_time": start_time, "end_time": end_time,
                "kind": "实验" if kind == "1" else "理论",
                "adjusted": str(row.get("ISTK", 0)) == "1",
            })
        except (KeyError, TypeError, ValueError):
            raise DataError("某条课程的周次、星期、节次或时间字段异常，已停止生成课表。") from None
    return result


def clock(value):
    if value in (None, ""):
        return ""
    value = str(value).strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", value):
        raise ValueError
    return value[:5]


def normalize_periods(rows):
    periods = {}
    try:
        for row in rows:
            if str(row.get("SFSY", 1)) != "1":
                continue
            number = str(int(row["DM"]))
            value = {"start": clock(row["KSSJ"]), "end": clock(row["JSSJ"])}
            if not value["start"] or not value["end"] or value["start"] >= value["end"]:
                raise ValueError
            if number in periods and periods[number] != value:
                raise DataError("存在不同校区作息时间，需按校区适配后使用。")
            periods[number] = value
        if not periods:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise DataError("学校作息时间字段异常。") from None
    return periods


def validate_snapshot(snapshot):
    try:
        if snapshot["version"] != 1:
            raise ValueError
        if not re.fullmatch(r"\d{4}-\d{4}-[123]", snapshot["term"]):
            raise ValueError
        start = date.fromisoformat(snapshot["start_date"])
        fetched = datetime.fromisoformat(snapshot["fetched_at"])
        if start.weekday() != 0 or fetched.tzinfo is None or not 1 <= snapshot["total_weeks"] <= 54:
            raise ValueError
        if not isinstance(snapshot["courses"], list) or not isinstance(snapshot["periods"], dict):
            raise ValueError
        for c in snapshot["courses"]:
            if not c["name"] or not 1 <= c["weekday"] <= 7 or not 1 <= c["begin"] <= c["end"] <= 30:
                raise ValueError
            if not isinstance(c["weeks"], list) or any(type(w) is not int or not 1 <= w <= 60 for w in c["weeks"]):
                raise ValueError
            if c["kind"] not in ("理论", "实验"):
                raise ValueError
            if not c["start_time"] and (str(c["begin"]) not in snapshot["periods"] or str(c["end"]) not in snapshot["periods"]):
                raise ValueError
            a = clock(c["start_time"] or snapshot["periods"][str(c["begin"])]["start"])
            b = clock(c["end_time"] or snapshot["periods"][str(c["end"])]["end"])
            if not a or not b or a >= b:
                raise ValueError
        if not isinstance(snapshot["unplaced"], list) or not all(isinstance(s, str) for s in snapshot["unplaced"]):
            raise ValueError
        return snapshot
    except (KeyError, TypeError, ValueError):
        raise DataError("课表快照不完整或已损坏，请重新同步。") from None


def select_day(snapshot, target, overrides=None, use_live_calendar=False):
    validate_snapshot(snapshot)
    overrides = overrides or {}
    override = overrides.get(target.isoformat(), "missing")
    if override is None:
        return [], "手动设置为停课日"
    source_date = target
    if override != "missing":
        try:
            source_date = date.fromisoformat(override)
        except (TypeError, ValueError):
            raise DataError("调休映射必须是 YYYY-MM-DD 或 null。") from None
    start = date.fromisoformat(snapshot["start_date"])
    week = (source_date - start).days // 7 + 1
    weekday = source_date.isoweekday()
    if source_date == target and use_live_calendar:
        try:
            check = snapshot["calendar_check"]
            if check["date"] != target.isoformat():
                raise ValueError
            week, weekday = int(check["week"]), int(check["weekday"])
        except (KeyError, TypeError, ValueError):
            raise DataError("实时校历与目标日期不一致。") from None
    if not 1 <= week <= snapshot["total_weeks"] or not 1 <= weekday <= 7:
        raise DataError("目标日期不在已同步学期内，请更新课表。")
    courses = []
    seen = set()
    for item in snapshot["courses"]:
        if weekday != item["weekday"] or week not in item["weeks"]:
            continue
        c = dict(item)
        c["start_time"] = c["start_time"] or snapshot["periods"][str(c["begin"])]["start"]
        c["end_time"] = c["end_time"] or snapshot["periods"][str(c["end"])]["end"]
        identity = tuple(c[k] for k in ("name", "teacher", "room", "start_time", "end_time", "kind"))
        if identity not in seen:
            courses.append(c)
            seen.add(identity)
    courses.sort(key=lambda c: (c["start_time"], c["end_time"], c["name"]))
    note = f"第 {week} 周 · 星期{'一二三四五六日'[weekday - 1]}"
    if source_date != target:
        note += f" · 调休：按 {source_date.isoformat()} 上课"
    return courses, note


def allow_cache(snapshot, target, now, max_days):
    validate_snapshot(snapshot)
    age = now - datetime.fromisoformat(snapshot["fetched_at"])
    if age < timedelta(minutes=-5) or age > timedelta(days=max_days) or max_days == 0:
        raise DataError("备用课表已过期，不能确认今日安排，请重新同步。")
    select_day(snapshot, target)


def safe(text):
    return str(text).replace("\n", " ").replace("\r", " ")


def render(snapshot, target, courses, note, warning=""):
    title = f"{target:%m月%d日}课表｜{len(courses)}项安排" if courses else f"{target:%m月%d日}课表｜无已排课程"
    lines = [f"{target.isoformat()} 当日课表", f"{snapshot['term']} 学期 · {note}", ""]
    if warning:
        title = "【缓存】" + title
        lines += [f"提示：{safe(warning)}", ""]
    for c in courses:
        lines += [f"{c['start_time']}–{c['end_time']}　{safe(c['name'])}",
                  f"地点：{safe(c['room'] or '教务系统未注明')}",
                  f"教师：{safe(c['teacher'] or '教务系统未注明')}",
                  f"{c['kind']}课 · 第 {c['begin']}–{c['end']} 节" + (" · 有调课标记" if c['adjusted'] else ""), ""]
    if not courses:
        lines += ["教务系统课表中，当日没有已安排课程。", ""]
    if snapshot["unplaced"]:
        lines += ["未排入课表的课程", "这些课程未提供完整课表安排，请留意老师通知；不能据此认定今天上课。",
                  "、".join(safe(s) for s in snapshot["unplaced"]), ""]
    fetched = datetime.fromisoformat(snapshot["fetched_at"]).astimezone(TZ)
    lines += [f"数据同步：{fetched:%Y-%m-%d %H:%M}（北京时间）", "临时调课、考试和未录入教务系统的安排，以学校通知为准。"]
    return title, "\n".join(lines)
