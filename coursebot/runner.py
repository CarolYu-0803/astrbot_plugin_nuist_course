from .errors import CourseError
from .schedule import allow_cache, render, select_day, validate_snapshot
from .school import SchoolClient
from .storage import now, read_json, write_json


def school_client(cfg):
    return SchoolClient(cfg["state_dir"] / "session.json", cfg["request_timeout_seconds"],
                        auto_recover=cfg.get("auto_sso_recovery", True))


def keep_alive(cfg):
    client = school_client(cfg)
    try:
        return client.keep_alive()
    finally:
        client.close()


def sync(cfg, target):
    client = school_client(cfg)
    try:
        snapshot = validate_snapshot(client.fetch(target))
    finally:
        client.close()
    write_json(cfg["state_dir"] / "snapshot.json", snapshot)
    return snapshot


def prepare(cfg, target, offline=False):
    warning = ""
    live = False
    try:
        if offline:
            raise CourseError("离线预览")
        snapshot = sync(cfg, target)
        live = True
    except CourseError as error:
        cache_path = cfg["state_dir"] / "snapshot.json"
        if not cache_path.exists():
            raise error
        snapshot = read_json(cache_path)
        allow_cache(snapshot, target, now(), cfg["cache_max_age_days"])
        warning = f"{error} 使用备用课表；临时调课可能尚未同步。请重新登录或修复连接。"
    courses, note = select_day(snapshot, target, cfg.get("date_overrides"), use_live_calendar=live)
    title, body = render(snapshot, target, courses, note, warning)
    return title, body, "cache" if warning else "live"


def build_message(cfg, target):
    """Build the same plain-text timetable for commands and scheduled messages."""
    try:
        title, body, _ = prepare(cfg, target)
    except CourseError as error:
        title = f"{target:%m月%d日}课表获取失败，请检查"
        body = (f"今天的课表无法确认：{error}\n\n"
                "这不代表今天没课。请打开学校教务系统查看；请管理员运行 login 更新会话，"
                "并将 session.json 和 snapshot.json 放入插件配置的 state_dir。")
    return title + "\n\n" + body

