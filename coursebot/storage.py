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
        if type(data.get("auto_login_enabled", False)) is not bool:
            raise ValueError
        data["auto_login_enabled"] = data.get("auto_login_enabled", False)
        for name, default, lower, upper in (
            ("login_code_timeout_seconds", 180, 60, 600),
            ("login_retry_minutes", 30, 5, 1440),
            ("session_check_minutes", 60, 5, 1440),
        ):
            data[name] = int(data.get(name, default))
            if not lower <= data[name] <= upper:
                raise ValueError
        if data.get("login_browser_channel", "") not in ("", "chrome", "msedge"):
            raise ValueError
    except (ValueError, TypeError):
        raise CourseError("配置格式错误：缓存天数 0–180，请求超时 1–120 秒，验证码等待 60–600 秒，登录重试及检查间隔 5–1440 分钟；浏览器通道留空、chrome 或 msedge。") from None
    data["state_dir"].mkdir(parents=True, exist_ok=True, mode=0o700)
    return data

