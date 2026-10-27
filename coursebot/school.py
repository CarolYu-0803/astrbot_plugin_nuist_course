import re
import time
from datetime import date
from http.cookiejar import Cookie
from urllib.parse import urljoin, urlparse, urldefrag

import httpx

from .errors import DataError, LoginExpired, CourseError
from .storage import now, read_json, write_json

HOST = "client.vpn.nuist.edu.cn"
ENTRY = "https://client.vpn.nuist.edu.cn/https/webvpnd9ac59bfe36922205aef0fd413a20364/index.html#/"
BASE_PATTERN = r"https://client\.vpn\.nuist\.edu\.cn/https/webvpn[a-f0-9]+/jwapp/sys/wdkb"


def check_base(base):
    if not isinstance(base, str) or not re.fullmatch(BASE_PATTERN, base):
        raise DataError("会话中的课表网址无效，请重新运行 login。")
    return base


def rows(payload, name):
    try:
        if str(payload["code"]) != "0":
            raise ValueError
        block = payload["datas"][name]
        result = block["rows"]
        if not isinstance(result, list) or not all(isinstance(x, dict) for x in result):
            raise ValueError
        # EMAP extParams.code=1 is a successful response, NOT an error.
        total = int(block.get("totalSize", -1))
        if total > len(result):
            raise DataError(f"{name} 数据被分页截断，拒绝生成不完整课表。")
        return result
    except (KeyError, TypeError, ValueError):
        raise DataError(f"{name} 接口结构异常，可能登录失效或学校改版。") from None


class SchoolClient:
    def __init__(self, session_path, timeout=25, transport=None, *, auto_recover=True):
        self.path = session_path
        if not session_path.exists():
            raise LoginExpired("尚未保存学校会话，请先运行 login 并同步 session.json。")
        self.session = read_json(session_path)
        self.base = check_base(self.session.get("base_url"))
        self.auto_recover = auto_recover
        self.recovered = False
        self._recovery_attempted = False
        self.timeout = timeout
        self.http = httpx.Client(timeout=timeout, follow_redirects=False, transport=transport)
        self.http.headers.update({"User-Agent": self.session.get("user_agent", "Mozilla/5.0"),
                                  "Referer": self.base + "/*default/index.do",
                                  "Origin": "https://" + HOST,
                                  "X-Requested-With": "XMLHttpRequest"})
        for item in self.session.get("cookies", []):
            if item.get("domain", "").lstrip(".") not in {HOST, "vpn.nuist.edu.cn", "nuist.edu.cn"}:
                continue
            expiry = item.get("expires", -1)
            self.http.cookies.jar.set_cookie(Cookie(
                version=0, name=item["name"], value=item["value"], port=None, port_specified=False,
                domain=item["domain"], domain_specified=True,
                domain_initial_dot=item["domain"].startswith("."), path=item.get("path", "/"),
                path_specified=True, secure=item.get("secure", True),
                expires=int(expiry) if expiry and expiry > 0 else None,
                discard=not expiry or expiry <= 0, comment=None, comment_url=None,
                rest={"HttpOnly": item.get("httpOnly", True)}, rfc2109=False))

    def close(self):
        self.http.close()

    def save_session(self):
        self.session["cookies"] = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
             "secure": c.secure, "expires": c.expires or -1,
             "httpOnly": c.has_nonstandard_attr("HttpOnly")}
            for c in self.http.cookies.jar]
        self.session["updated_at"] = now().isoformat()
        write_json(self.path, self.session)

    def _navigate(self, url, deadline):
        """Follow ordinary SSO redirects, without sending cookies to other origins."""
        url = urldefrag(url)[0]
        for _ in range(10):
            parsed = urlparse(url)
            if (parsed.scheme != "https" or parsed.hostname != HOST or parsed.port not in (None, 443)
                    or parsed.username is not None or parsed.password is not None):
                raise LoginExpired("学校登录跳转离开已验证的 WebVPN 地址，无法自动续期，请重新运行 login。")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CourseError("学校登录续期超时，请检查网络或重新登录。")
            try:
                response = self.http.get(url, timeout=min(self.timeout, remaining),
                                         headers={"Accept": "text/html", "X-Requested-With": ""})
            except httpx.HTTPError:
                raise CourseError("连接学校登录入口失败或超时，请检查服务器网络。") from None
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise LoginExpired("学校登录入口缺少跳转地址，请重新运行 login。")
                url = urljoin(str(response.url), location)
                continue
            if response.status_code in (401, 403):
                raise LoginExpired("门户登录已失效，自动续期未成功，请重新运行 login。")
            if response.status_code != 200:
                raise CourseError(f"学校登录入口返回 HTTP {response.status_code}。")
            # A 200 HTML login page alone is never proof of a valid school session.
            return
        raise LoginExpired("学校登录跳转次数过多，可能需要验证码或重新登录。")

    def _recover_session(self):
        self._recovery_attempted = True
        deadline = time.monotonic() + 45
        self._navigate(ENTRY, deadline)
        academic = self.base.rsplit("/jwapp/", 1)[0] + "/jwapp/sys/emaphome/portal/index.do"
        self._navigate(academic, deadline)
        self._navigate(self.base + "/*default/index.do", deadline)

    def keep_alive(self):
        # Touch the portal as well as the authenticated teaching API: the two
        # sessions can have independent idle timeouts. Never refresh snapshot age here.
        portal_warning = ""
        try:
            self._navigate(ENTRY, time.monotonic() + 30)
        except CourseError as error:
            # Keep the teaching session alive even if the portal is unavailable.
            portal_warning = str(error)
        terms = self.query("jshkcb", "dqxnxq")
        if len(terms) != 1 or not re.fullmatch(r"\d{4}-\d{4}-[123]", str(terms[0].get("DM", ""))):
            raise DataError("保活未能确认当前学期，不能认定登录有效。")
        return {"recovered": self.recovered, "portal_warning": "" if self.recovered else portal_warning}

    def query(self, group, name, data=None):
        try:
            result = self._query_once(group, name, data)
        except LoginExpired:
            if not self.auto_recover or self._recovery_attempted:
                raise
            self._recover_session()
            # Replaying these read-only queries is safe. Verify the API, not an HTML page.
            result = self._query_once(group, name, data)
            self.recovered = True
        # Persist rotating cookies even if a later request in the full fetch fails.
        self.save_session()
        return result

    def _query_once(self, group, name, data=None):
        # These are the read-only POST endpoints observed in the school's own UI.
        url = f"{self.base}/modules/{group}/{name}.do?enlink-vpn"
        try:
            response = self.http.post(url, data=data or {})
        except httpx.HTTPError:
            raise CourseError("连接学校失败或超时，请检查服务器网络。") from None
        if response.is_redirect or response.status_code in (401, 403):
            raise LoginExpired("学校登录已失效，请重新运行 login 并上传会话文件。")
        if response.status_code != 200:
            raise CourseError(f"学校服务返回 HTTP {response.status_code}。")
        try:
            payload = response.json()
        except ValueError:
            raise LoginExpired("学校返回了登录页或非 JSON 页面，请更新登录状态。") from None
        if isinstance(payload, dict) and str(payload.get("code")) in {"401", "403"}:
            raise LoginExpired("学校接口提示登录已失效，请重新运行 login。")
        return rows(payload, name)

    def fetch(self, target):
        terms = self.query("jshkcb", "dqxnxq")
        if len(terms) != 1:
            raise DataError("无法唯一确定当前学期。")
        term = terms[0]
        try:
            term_id, year, semester = term["DM"], term["XNDM"], term["XQDM"]
            if not re.fullmatch(r"\d{4}-\d{4}-[123]", term_id):
                raise ValueError
            query = {"XN": year, "XQ": semester}
            calendars = self.query("xskcb", "cxxljc", query)
            if len(calendars) != 1:
                raise DataError("校历返回多个或零个记录，无法确定开学日期。")
            start = date.fromisoformat(calendars[0]["XQKSRQ"][:10])
            total_weeks = int(calendars[0]["ZZC"])
            if not 1 <= total_weeks <= 54 or start.weekday() != 0:
                raise ValueError
            current = self.query("jshkcb", "dqzc", {**query, "RQ": target.isoformat()})
            expected = (target - start).days // 7 + 1
            if not 1 <= expected <= total_weeks:
                raise DataError("目标日期不在学校当前学期校历内，不能判定为无课。")
            if len(current) != 1 or str(current[0]["RQ"])[:10] != target.isoformat():
                raise DataError("学校没有返回目标日期对应的教学周。")
            week, weekday = int(current[0]["ZC"]), int(current[0]["XQJ"])
            if not 1 <= week <= total_weeks or not 1 <= weekday <= 7:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise DataError("学校校历字段变化，拒绝猜测教学周。") from None
        courses = self.query("xskcb", "cxxszhxqkb", {"XNXQDM": term_id})
        periods = self.query("jshkcb", "jc")
        unplaced = self.query("xskcb", "xswpkc", {"XNXQDM": term_id})
        from .schedule import normalize_courses, normalize_periods
        snapshot = {
            "version": 1, "term": term_id, "start_date": start.isoformat(),
            "total_weeks": total_weeks, "fetched_at": now().isoformat(),
            "calendar_check": {"date": target.isoformat(), "week": week, "weekday": weekday},
            "courses": normalize_courses(courses), "periods": normalize_periods(periods),
            "unplaced": list(dict.fromkeys(str(r["KCM"]) for r in unplaced if r.get("KCM"))),
        }
        self.save_session()
        return snapshot

