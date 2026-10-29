"""Headless school login using the school's own form and encryption scripts."""

import asyncio
import os
import re
from contextlib import suppress

from .errors import CourseError, CredentialsRejected
from .login import ACADEMIC_ENTRY, TIMETABLE_ENTRY, is_school_url
from .school import ENTRY, HOST, check_base
from .storage import now, read_json

PASSWORD_FORM = "#pwdFromId"
SMS_INPUTS = (
    "#dynamicCode", "#smsCode", "#verifyCode", "input[name='dynamicCode']",
    "input[name='smsCode']", "input[placeholder*='短信']", "input[placeholder*='动态码']",
)


def credentials(cfg):
    username = os.environ.get("NUIST_COURSE_USERNAME") or cfg.get("login_username", "")
    password = os.environ.get("NUIST_COURSE_PASSWORD") or cfg.get("login_password", "")
    if not isinstance(username, str) or not username.strip() or not isinstance(password, str) or not password:
        raise CourseError("请在插件配置或 NUIST_COURSE_USERNAME / NUIST_COURSE_PASSWORD 环境变量中填写学校账号密码。")
    return username.strip(), password


async def visible(page, selectors):
    for selector in selectors:
        for item in await page.locator(selector).all():
            if await item.is_visible():
                return item
    return None


def trusted(page):
    if not is_school_url(page.url):
        raise CourseError("学校登录跳转到了未适配的站点，已停止输入凭据。请使用人工登录助手。")


async def submit_form(page, button):
    # A document marker also detects a form reload at the very same URL.
    before = await page.evaluate("""() => ({url: location.href,
        errors: [...document.querySelectorAll('#showErrorTip, .form-error')]
            .filter(e => e.offsetWidth).map(e => e.textContent.trim()).join('|'),
        sms: [...document.querySelectorAll('input')].some(e => e.offsetWidth &&
            /smsCode|dynamicCode|verifyCode/.test(e.id + ' ' + e.name)),
        slider: !!document.querySelector('#sliderCaptchaDiv')?.offsetWidth,
        trust: [...document.querySelectorAll('button,a')].some(e =>
            e.offsetWidth && e.textContent.trim() === '信任此设备')
    })""")
    await page.evaluate("window.__nuistCourseDocument = true")
    await button.click()
    await page.wait_for_function("""before => !window.__nuistCourseDocument ||
        location.href !== before.url ||
        [...document.querySelectorAll('#showErrorTip, .form-error')]
            .filter(e => e.offsetWidth).map(e => e.textContent.trim()).join('|') !== before.errors ||
        (!before.sms && [...document.querySelectorAll('input')].some(e => e.offsetWidth &&
            /smsCode|dynamicCode|verifyCode/.test(e.id + ' ' + e.name))) ||
        (!before.slider && !!document.querySelector('#sliderCaptchaDiv')?.offsetWidth) ||
        (!before.trust && [...document.querySelectorAll('button,a')].some(e =>
            e.offsetWidth && e.textContent.trim() === '信任此设备'))
    """, arg=before, timeout=30000)
    await page.wait_for_load_state("domcontentloaded")


class BrowserLogin:
    def __init__(self, cfg, request):
        self.cfg = cfg
        self.request = request
        self.password_submissions = 0
        self.sms_submissions = 0
        self.sms_sent = False

    async def _password(self, page):
        if self.password_submissions >= 3:
            raise CourseError("图片验证码多次未通过，已停止本次登录。")
        trusted(page)
        username, password = credentials(self.cfg)
        await page.locator(PASSWORD_FORM + " #username").fill(username)
        await page.locator(PASSWORD_FORM + " #password").fill(password)
        captcha = await visible(page, [PASSWORD_FORM + " #captcha"])
        if captcha:
            image = page.locator(PASSWORD_FORM + " #captchaImg")
            await image.wait_for(state="visible")
            await page.wait_for_function("""() => {
                const e = document.querySelector('#pwdFromId #captchaImg');
                return e && e.complete && e.naturalWidth > 0;
            }""")
            code = await self.request("image", await image.screenshot())
            trusted(page)
            await captcha.fill(code)
        self.password_submissions += 1
        await submit_form(page, page.locator(PASSWORD_FORM + " #login_submit"))
        error = await visible(page, [PASSWORD_FORM + " #showErrorTip"])
        if error:
            text = (await error.inner_text()).strip()
            credential_error = re.search(r"(密码|用户|账号|账户).*(错误|不正确|失败|锁定|冻结|禁用|不存在)", text)
            if text and (credential_error or "验证码" not in text):
                # Never return school text: it may contain the account identifier.
                raise CredentialsRejected("学校拒绝了账号登录。自动重试已暂停，请检查密码或账号状态后发送 /课表登录。")

    async def _sms(self, page, field):
        if self.sms_submissions >= 2:
            raise CourseError("短信验证多次未通过，已停止本次登录。")
        trusted(page)
        # Click once per page, never repeatedly send SMS while waiting for input.
        if not self.sms_sent:
            send = page.get_by_text(re.compile(r"^(获取|发送|重新发送)(短信)?(验证码|动态码)$"))
            for item in await send.all():
                if await item.is_visible() and await item.is_enabled():
                    await item.click()
                    self.sms_sent = True
                    break
        code = await self.request("sms")
        trusted(page)
        await field.fill(code)
        remember = page.get_by_label(re.compile("信任.*设备|记住.*设备|不再验证"))
        for item in await remember.all():
            if await item.is_visible() and await item.get_attribute("type") == "checkbox":
                await item.check()
        button = await visible(page, ["#reAuthSubmitBtn", "#verify_submit", "#submit", "#login_submit", "button[type='submit']", "input[type='submit']"])
        if button is None:
            candidates = page.get_by_text(re.compile(r"^(验证|确认|确定|继续|登录|确认登录|提交)$"))
            for item in await candidates.all():
                if await item.is_visible():
                    button = item
                    break
        if button is None:
            raise CourseError("尚未适配学校短信验证的提交按钮，请使用人工登录助手。")
        self.sms_submissions += 1
        await submit_form(page, button)

    async def authenticate(self, page):
        for _ in range(7):
            trusted(page)
            trust = page.get_by_text("信任此设备", exact=True)
            trust_button = None
            for item in await trust.all():
                if await item.is_visible():
                    trust_button = item
                    break
            if trust_button is not None:
                await submit_form(page, trust_button)
                continue
            if await visible(page, [PASSWORD_FORM + " #username"]):
                await self._password(page)
                continue
            field = await visible(page, SMS_INPUTS)
            if field:
                await self._sms(page, field)
                continue
            if await visible(page, ["#sliderCaptchaDiv"]):
                raise CourseError("学校要求滑块验证，当前版本请使用人工登录助手完成。")
            return
        raise CourseError("学校登录步骤超出限制，请使用人工登录助手检查验证页面。")

    async def run(self):
        credentials(self.cfg)
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise CourseError("自动登录需要 Playwright，请在 AstrBot 的 Python 环境安装 playwright，并运行 python -m playwright install chromium。") from None
        browser = None
        async with async_playwright() as pw:
            try:
                options = {"headless": True}
                if self.cfg.get("login_browser_channel"):
                    options["channel"] = self.cfg["login_browser_channel"]
                try:
                    browser = await pw.chromium.launch(**options)
                except Exception:
                    raise CourseError("自动登录浏览器无法启动。请在 AstrBot 的 Python 环境执行 python -m playwright install --with-deps chromium，或配置已安装的浏览器通道。") from None
                state_path = self.cfg["state_dir"] / "browser_state.json"
                options = {"locale": "zh-CN", "timezone_id": "Asia/Shanghai"}
                if state_path.exists():
                    options["storage_state"] = read_json(state_path)
                context = await browser.new_context(**options)
                page = await context.new_page()
                page.set_default_timeout(25000)
                await page.goto(ENTRY, wait_until="domcontentloaded", timeout=60000)
                await self.authenticate(page)
                await page.goto(ACADEMIC_ENTRY, wait_until="domcontentloaded", timeout=60000)
                await self.authenticate(page)
                await page.wait_for_url(lambda url: is_school_url(str(url)) and
                                        "/jwapp/sys/emaphome/portal/" in str(url), timeout=25000)
                await page.goto(TIMETABLE_ENTRY, wait_until="domcontentloaded", timeout=60000)
                await self.authenticate(page)
                await page.locator("#print_area table").wait_for()
                await page.locator('[data-action="理实一体"]').click()
                switch = page.locator('[data-action="周课表周课表"]')
                if await switch.is_visible():
                    await switch.click()
                await page.locator('[data-action="周课表切换"]').wait_for(state="visible")
                trusted(page)
                base = check_base(page.url.split("/jwapp/sys/wdkb/")[0] + "/jwapp/sys/wdkb")
                cookies = [c for c in await context.cookies() if c["domain"].lstrip(".") in
                           {HOST, "vpn.nuist.edu.cn", "nuist.edu.cn"}]
                session = {"version": 1, "base_url": base, "cookies": cookies,
                           "user_agent": await page.evaluate("navigator.userAgent"),
                           "updated_at": now().isoformat()}
                return session, await context.storage_state()
            except (CourseError, asyncio.CancelledError):
                raise
            except Exception:
                raise CourseError("学校自动登录未完成，可能是网络超时或验证页面变化。可用 /课表登录 重试，或使用人工登录助手。") from None
            finally:
                if browser is not None:
                    with suppress(Exception):
                        await browser.close()
