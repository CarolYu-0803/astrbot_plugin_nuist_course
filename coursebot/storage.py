import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .errors import CourseError

TZ = ZoneInfo("Asia/Shanghai")


def now():
    return datetime.now(TZ)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise CourseError(f"无法读取或解析 {Path(path).name}，请检查文件。") from None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=".course-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def config(path):
    path = Path(path).resolve()
    return config_values(read_json(path), path.parent)


def config_values(data, base_dir):
    if not isinstance(data, dict):
        raise CourseError("课表配置必须是 JSON 对象。")
    data = dict(data)
    try:
        data["state_dir"] = (Path(base_dir) / (data.get("state_dir") or "state")).resolve()
        data["cache_max_age_days"] = int(data.get("cache_max_age_days", 7))
        data["request_timeout_seconds"] = int(data.get("request_timeout_seconds", 25))
        if not 0 <= data["cache_max_age_days"] <= 180:
            raise ValueError
        if not 1 <= data["request_timeout_seconds"] <= 120:
            raise ValueError
        if not isinstance(data.get("date_overrides", {}), dict):
            raise ValueError
    except (ValueError, TypeError):
        raise CourseError("配置格式错误：缓存天数应为 0–180，超时应为 1–120 秒。") from None
    try:
        interval = data.get("keepalive_interval_minutes", 5)
        if (isinstance(interval, bool) or not (isinstance(interval, int)
                or (isinstance(interval, str) and interval.isascii() and interval.isdigit()))):
            raise ValueError
        data["keepalive_interval_minutes"] = int(interval)
        if not 0 <= data["keepalive_interval_minutes"] <= 20:
            raise ValueError
        data["auto_sso_recovery"] = data.get("auto_sso_recovery", True)
        if type(data["auto_sso_recovery"]) is not bool:
            raise ValueError
    except (TypeError, ValueError):
        raise CourseError("保活配置错误：间隔须为 0–20 的整数分钟（0 关闭），自动恢复登录须为布尔值。") from None
    data["state_dir"].mkdir(parents=True, exist_ok=True, mode=0o700)
    return data

