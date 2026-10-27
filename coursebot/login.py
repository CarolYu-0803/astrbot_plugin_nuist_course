import queue
import threading
from urllib.parse import urlparse

from .errors import CourseError
from .school import ENTRY, HOST, check_base
from .storage import now, write_json

# URLs observed after the user's successful portal -> teaching system -> timetable
# navigation. These contain no ticket or account identifier. Keeping the same tab
# avoids depending on the portal/WebVPN's popup initialization and opener chain.
ACADEMIC_ROOT = (
    "https://client.vpn.nuist.edu.cn/https/"
    "webvpn0852a5f822ad5ca19fb52006c843ea2e7397e76d41c77f8a91f1345208e4e34b"
)
ACADEMIC_ENTRY = ACADEMIC_ROOT + "/jwapp/sys/emaphome/portal/index.do"
TIMETABLE_ENTRY = ACADEMIC_ROOT + "/jwapp/sys/wdkb/*default/index.do?THEME=indigo&EMAP_LANG=zh#/xskcb"


def wait_for_enter(context, prompt, reader=None):
    """Keep Playwright dispatching while the user operates the browser.

    Only terminal input runs in the daemon thread; all Playwright calls remain
    on their owning thread. No credentials are read from the browser or stdin.
    """
    results = queue.Queue(maxsize=1)
    reader = input if reader is None else reader

    def read():
        try:
            reader(prompt)
            results.put(None)
        except Exception as error:
            results.put(error)

    threading.Thread(target=read, daemon=True).start()
    while True:
        try:
            result = results.get_nowait()
        except queue.Empty:
            pages = [p for p in context.pages if not p.is_closed()]
            if not pages:
                raise CourseError("浏览器窗口已关闭，请重新运行 login。")
            try:
                pages[0].wait_for_timeout(100)
            except Exception:
                if not any(not p.is_closed() for p in context.pages):
                    raise CourseError("浏览器窗口已关闭，请重新运行 login。") from None
            continue
        if result is not None:
            raise CourseError("无法读取回车确认，请在交互式 PowerShell 或终端中运行 login。") from None
        return


def is_school_url(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == HOST and parsed.port in (None, 443)


def is_course_page(page):
    return not page.is_closed() and is_school_url(page.url) and "/jwapp/sys/wdkb/" in urlparse(page.url).path


def navigation_page(context):
    """Use an existing authenticated school page, never the empty popup."""
    candidates = [p for p in context.pages if not p.is_closed() and is_school_url(p.url)
                  and (urlparse(p.url).path == urlparse(ENTRY).path or "/jwapp/" in urlparse(p.url).path)]
    return candidates[-1] if candidates else None


def open_timetable(page):
    """Ordinary same-tab navigation through known school routes, preserving SSO."""
    if not is_course_page(page):
        page.goto(ACADEMIC_ENTRY, wait_until="domcontentloaded", timeout=60000)
        # If the school requests additional authentication, leave it visible.
        # The outer login loop waits for the user and permits another attempt.
        page.wait_for_url(lambda url: is_school_url(str(url)) and
                          "/jwapp/sys/emaphome/portal/" in urlparse(str(url)).path,
                          timeout=20000)
        page.goto(TIMETABLE_ENTRY, wait_until="domcontentloaded", timeout=60000)
    page.locator("#print_area table").wait_for(timeout=20000)
    page.locator('[data-action="理实一体"]').click(timeout=15000)
    switch = page.locator('[data-action="周课表周课表"]')
    if switch.is_visible():
        switch.click(timeout=15000)
    page.locator('[data-action="周课表切换"]').wait_for(state="visible", timeout=15000)
    page.locator("#print_area table").wait_for(timeout=15000)
    page.bring_to_front()
    return page


def login(cfg, channel=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise CourseError('请先安装登录组件：python -m pip install ".[login]"') from None
    with sync_playwright() as pw:
        options = {"headless": False}
        if channel:
            options["channel"] = channel
        try:
            browser = pw.chromium.launch(**options)
        except Exception:
            raise CourseError("无法启动浏览器。请运行 python -m playwright install chromium，或使用 login --channel msedge。") from None
        context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai")
        page = context.new_page()
        try:
            page.goto(ENTRY, wait_until="domcontentloaded", timeout=60000)
            print("请在新浏览器窗口中自行完成统一身份认证，看到信息门户后回到这里。")
            print("程序会在同一标签页打开教务与课表；无需点击门户里的本科生教学管理系统。")
            print("如果已经打开了 about:blank，也可直接回到这里按回车，保留原门户页即可。")
            while True:
                wait_for_enter(context, "登录完成后按回车打开课表（Ctrl+C 取消）：")
                candidates = [p for p in context.pages if is_course_page(p)]
                if len(candidates) > 1:
                    print("请只保留一个我的课表页面，然后按回车继续。")
                    continue
                page = candidates[0] if candidates else navigation_page(context)
                if page is None:
                    print("尚未找到信息门户或教务页面，请先在浏览器中完成统一身份认证。")
                    continue
                try:
                    page = open_timetable(page)
                except Exception:
                    print("课表尚未加载完成。浏览器仍保留，请检查当前页是否要求再次登录。")
                    print("也可在浏览器中手动进入我的课表；完成后回到这里按回车重试。")
                    continue
                wait_for_enter(context, "请核对浏览器中的理实一体周课表，确认后按回车保存会话：")
                if not is_course_page(page) or not page.locator("#print_area table").is_visible():
                    print("课表页已关闭或发生跳转，请重新打开后继续。")
                    continue
                break
            base = check_base(page.url.split("/jwapp/sys/wdkb/")[0] + "/jwapp/sys/wdkb")
            cookies = [c for c in context.cookies() if c["domain"].lstrip(".") in
                       {HOST, "vpn.nuist.edu.cn", "nuist.edu.cn"}]
            write_json(cfg["state_dir"] / "session.json", {
                "version": 1, "base_url": base, "cookies": cookies,
                "user_agent": page.evaluate("navigator.userAgent"), "updated_at": now().isoformat(),
            })
        except CourseError:
            raise
        except (EOFError, KeyboardInterrupt):
            raise CourseError("登录已取消。") from None
        except Exception:
            raise CourseError("登录或课表页面读取失败，请重新运行 login。") from None
        finally:
            browser.close()
    from .runner import sync
    snapshot = sync(cfg, now().date())
    print(f"已保存会话并同步 {snapshot['term']} 学期课表。请妥善保管 state 目录。")

