from .errors import CourseError, LoginExpired
from .schedule import allow_cache, render, select_day, validate_snapshot
from .school import SchoolClient
from .storage import now, read_json, write_json


def sync(cfg, target):
    client = SchoolClient(cfg["state_dir"] / "session.json", cfg["request_timeout_seconds"])
    try:
        snapshot = validate_snapshot(client.fetch(target))
    finally:
        client.close()
    write_json(cfg["state_dir"] / "snapshot.json", snapshot)
    return snapshot


def prepare_timetable(cfg, target, offline=False, *, defer_login=False):
    """Return timetable data with the same calendar and cache checks as push messages."""
    warning = ""
    live = False
    try:
        if offline:
            raise CourseError("离线预览")
        snapshot = sync(cfg, target)
        live = True
    except CourseError as error:
        if defer_login and isinstance(error, LoginExpired):
            raise
        cache_path = cfg["state_dir"] / "snapshot.json"
        if not cache_path.exists():
            raise error
        snapshot = read_json(cache_path)
        allow_cache(snapshot, target, now(), cfg["cache_max_age_days"])
        warning = f"{error} 使用备用课表；临时调课可能尚未同步。请重新登录或修复连接。"
    courses, note = select_day(snapshot, target, cfg.get("date_overrides"), use_live_calendar=live)
    return {
        "date": target.isoformat(),
        "timezone": "Asia/Shanghai",
        "term": snapshot["term"],
        "calendar_note": note,
        "source": "cache" if warning else "live",
        "warning": warning,
        "fetched_at": snapshot["fetched_at"],
        "courses": courses,
        "unplaced": snapshot["unplaced"],
        "notice": "临时调课、考试和未录入教务系统的安排，以学校通知为准；未排课程不能据此认定当天上课。",
    }


def prepare(cfg, target, offline=False, *, defer_login=False):
    timetable = prepare_timetable(cfg, target, offline, defer_login=defer_login)
    snapshot = {"term": timetable["term"], "fetched_at": timetable["fetched_at"],
                "unplaced": timetable["unplaced"]}
    title, body = render(snapshot, target, timetable["courses"],
                         timetable["calendar_note"], timetable["warning"])
    return title, body, timetable["source"]


def build_message(cfg, target, *, defer_login=False):
    """Build the same plain-text timetable for commands and scheduled messages."""
    try:
        title, body, _ = prepare(cfg, target, defer_login=defer_login)
    except CourseError as error:
        if defer_login and isinstance(error, LoginExpired):
            raise
        title = f"{target:%m月%d日}课表获取失败，请检查"
        body = (f"今天的课表无法确认：{error}\n\n"
                "这不代表今天没课。请打开学校教务系统查看；请管理员检查 /课表登录状态，"
                "或使用人工登录助手更新 state_dir 中的会话与快照。")
    return title + "\n\n" + body

