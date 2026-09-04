# -*- coding: utf-8 -*-
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import Playwright, TimeoutError as PlaywrightTimeoutError, async_playwright
import os
import asyncio

from conf import LOCAL_CHROME_PATH, LOCAL_CHROME_HEADLESS
from uploader.tk_uploader.tk_config import Tk_Locator
from utils.base_social_media import set_init_script


TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?lang=en"
TIKTOK_UPLOAD_URL_PATTERN = "**/tiktokstudio/upload**"
TIKTOK_CONTENT_URL_PATTERN = "**/tiktokstudio/content**"
from utils.files_times import get_absolute_path
from utils.log import tiktok_logger


async def cookie_auth(account_file):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=LOCAL_CHROME_HEADLESS)
        context = await browser.new_context(storage_state=account_file)
        context = await set_init_script(context)
        try:
            page = await context.new_page()
            navigation_error = None
            for attempt in range(1, 4):
                try:
                    await page.goto(
                        "https://www.tiktok.com/tiktokstudio/upload?lang=en",
                        wait_until="domcontentloaded",
                        timeout=60_000,
                    )
                    navigation_error = None
                    break
                except Exception as exc:
                    navigation_error = exc
                    tiktok_logger.warning(
                        f"[+] TikTok credential check connection retry {attempt}/3"
                    )
                    await page.wait_for_timeout(2000)
            if navigation_error is not None:
                raise RuntimeError(
                    "TikTok credential check could not reach the official site after 3 attempts"
                ) from navigation_error
            # TikTok continuously emits telemetry requests, so `networkidle`
            # is not a valid login signal. Use the authenticated session cookie
            # plus the final route/form instead.
            await page.wait_for_timeout(3000)
            cookie_names = {
                str(item.get("name") or "") for item in await context.cookies()
            }
            has_session = bool(
                cookie_names.intersection({"sessionid", "sessionid_ss", "sid_tt"})
            )
            current_url = str(page.url or "").lower()
            returned_to_login = "/login" in current_url
            upload_form_visible = bool(
                await page.locator(
                    'button:has-text("Select video"), '
                    'button[aria-label="Select file"], '
                    'input[type="file"]'
                ).count()
            )
            valid = has_session and not returned_to_login and (
                "/tiktokstudio" in current_url or upload_form_visible
            )
            if valid:
                tiktok_logger.success("[+] cookie valid")
                return True
            tiktok_logger.error("[+] cookie expired")
            return False
        finally:
            await context.close()
            await browser.close()


async def tiktok_setup(account_file, handle=False):
    account_file = get_absolute_path(account_file, "tk_uploader")
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            return False
        tiktok_logger.info('[+] cookie file is not existed or expired. Now open the browser auto. Please login with your way(gmail phone, whatever, the cookie file will generated after login')
        await get_tiktok_cookie(account_file)
    return True


async def get_tiktok_cookie(account_file):
    async with async_playwright() as playwright:
        options = {
            'args': [
                '--lang en-GB',
            ],
            'headless': LOCAL_CHROME_HEADLESS,  # Set headless option here
        }
        # Make sure to run headed.
        browser = await playwright.chromium.launch(**options)
        # Setup context however you like.
        context = await browser.new_context()  # Pass any options
        context = await set_init_script(context)
        # Pause the page, and start recording manually.
        page = await context.new_page()
        await page.goto("https://www.tiktok.com/login?lang=en")
        await page.pause()
        # 点击调试器的继续，保存cookie
        await context.storage_state(path=account_file)


class TiktokVideo(object):
    def __init__(
        self,
        title,
        file_path,
        tags,
        publish_date,
        account_file,
        thumbnail_path=None,
        dry_run=False,
        headless=None,
        preview_seconds=120,
    ):
        self.title = title
        self.file_path = file_path
        self.tags = tags
        self.publish_date = publish_date
        self.thumbnail_path = thumbnail_path
        self.account_file = account_file
        self.local_executable_path = LOCAL_CHROME_PATH
        self.dry_run = bool(dry_run)
        self.headless = LOCAL_CHROME_HEADLESS if headless is None else bool(headless)
        self.preview_seconds = max(0, int(preview_seconds))
        self.locator_base = None

    async def set_schedule_time(self, page, publish_date):
        schedule_input_element = self.locator_base.get_by_label('Schedule')
        await schedule_input_element.wait_for(state='visible')  # 确保按钮可见

        await schedule_input_element.click(force=True)
        if await self.locator_base.locator('div.TUXButton-content >> text=Allow').count():
            await self.locator_base.locator('div.TUXButton-content >> text=Allow').click()

        scheduled_picker = self.locator_base.locator('div.scheduled-picker')
        await scheduled_picker.locator('div.TUXInputBox').nth(1).click()

        calendar_month = await self.locator_base.locator(
            'div.calendar-wrapper span.month-title').inner_text()

        n_calendar_month = datetime.strptime(calendar_month, '%B').month

        schedule_month = publish_date.month

        if n_calendar_month != schedule_month:
            if n_calendar_month < schedule_month:
                arrow = self.locator_base.locator('div.calendar-wrapper span.arrow').nth(-1)
            else:
                arrow = self.locator_base.locator('div.calendar-wrapper span.arrow').nth(0)
            await arrow.click()

        # day set
        valid_days_locator = self.locator_base.locator(
            'div.calendar-wrapper span.day.valid')
        valid_days = await valid_days_locator.count()
        for i in range(valid_days):
            day_element = valid_days_locator.nth(i)
            text = await day_element.inner_text()
            if text.strip() == str(publish_date.day):
                await day_element.click()
                break
        # time set
        await scheduled_picker.locator('div.TUXInputBox').nth(0).click()

        hour_str = publish_date.strftime("%H")
        correct_minute = int(publish_date.minute / 5)
        minute_str = f"{correct_minute:02d}"

        hour_selector = f"span.tiktok-timepicker-left:has-text('{hour_str}')"
        minute_selector = f"span.tiktok-timepicker-right:has-text('{minute_str}')"

        # pick hour first
        await page.wait_for_timeout(1000)  # 等待500毫秒
        await self.locator_base.locator(hour_selector).click()
        # click time button again
        await page.wait_for_timeout(1000)  # 等待500毫秒
        # pick minutes after
        await self.locator_base.locator(minute_selector).click()

        # click title to remove the focus.
        # await self.locator_base.locator("h1:has-text('Upload video')").click()

    async def handle_upload_error(self, page):
        tiktok_logger.info("video upload error retrying.")
        select_file_button = self.locator_base.locator('button[aria-label="Select file"]')
        async with page.expect_file_chooser() as fc_info:
            await select_file_button.click()
        file_chooser = await fc_info.value
        await file_chooser.set_files(self.file_path)

    async def upload(self, playwright: Playwright) -> None:
        launch_kwargs = {"headless": self.headless}
        executable_value = str(self.local_executable_path or "").strip()
        if executable_value not in {"", "."}:
            launch_kwargs["executable_path"] = executable_value
        browser = await playwright.chromium.launch(**launch_kwargs)
        context = await browser.new_context(storage_state=f"{self.account_file}")
        # context = await set_init_script(context)
        page = await context.new_page()

        navigation_error = None
        for attempt in range(1, 4):
            try:
                await page.goto(
                    TIKTOK_UPLOAD_URL,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                navigation_error = None
                break
            except Exception as exc:
                navigation_error = exc
                tiktok_logger.warning(
                    f"[+] TikTok upload page connection retry {attempt}/3"
                )
                await page.wait_for_timeout(2000)
        if navigation_error is not None:
            raise RuntimeError(
                "TikTok upload page could not be reached after 3 attempts"
            ) from navigation_error
        tiktok_logger.info(f'[+]Uploading-------{self.title}.mp4')

        await page.wait_for_url(TIKTOK_UPLOAD_URL_PATTERN, timeout=10000)

        try:
            await page.wait_for_selector('iframe[data-tt="Upload_index_iframe"], div.upload-container', timeout=10000)
            tiktok_logger.info("Either iframe or div appeared.")
        except Exception as e:
            tiktok_logger.error("Neither iframe nor div appeared within the timeout.")

        await self.choose_base_locator(page)

        upload_button = self.locator_base.locator(
            'button:has-text("Select video"), button:has-text("选择视频")'
        ).first
        if await upload_button.count():
            await upload_button.wait_for(state="visible")
            async with page.expect_file_chooser() as fc_info:
                await upload_button.click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(self.file_path)
        else:
            # TikTok may render the upload page in Chinese even with `lang=en`.
            # Fall back to its stable video input instead of assuming button text.
            video_input = self.locator_base.locator(
                'input[type="file"][accept*="video"]'
            ).first
            await video_input.wait_for(state="attached", timeout=30000)
            await video_input.set_input_files(self.file_path)

        await self.dismiss_upload_interstitials(page)
        await self.add_title_tags(page)
        # detect upload status
        await self.detect_upload_status(page)
        # Some TikTok prompts are mounted only after transcoding completes.
        await self.dismiss_upload_interstitials(page)
        if self.thumbnail_path:
            tiktok_logger.info(f'[+] Uploading thumbnail file {self.title}.png')
            await self.upload_thumbnails(page)

        if self.publish_date != 0:
            await self.set_schedule_time(page, self.publish_date)

        if self.dry_run:
            tiktok_logger.warning("[dry-run] Form is ready; skipped the Post button")
            preview_path = str(Path(self.account_file).with_name("tiktok_dry_run_preview.png"))
            try:
                await page.screenshot(full_page=True, path=preview_path)
                tiktok_logger.info(f"[dry-run] Preview saved: {preview_path}")
            except Exception as exc:
                tiktok_logger.warning(f"[dry-run] Preview capture failed: {exc}")
            if self.preview_seconds:
                await page.wait_for_timeout(self.preview_seconds * 1000)
            await context.storage_state(path=f"{self.account_file}")
            await context.close()
            await browser.close()
            return

        await self.click_publish(page)
        public_url = await self.get_published_video_url(page)
        if public_url:
            tiktok_logger.success(f"published_url: {public_url}")
        else:
            tiktok_logger.warning(
                "TikTok accepted the submission but its public URL is not visible yet"
            )

        await context.storage_state(path=f"{self.account_file}")  # save cookie
        tiktok_logger.info('  [-] update cookie！')
        await asyncio.sleep(2)  # close delay for look the video status
        # close all
        await context.close()
        await browser.close()
        if public_url:
            return {
                "status": "success",
                "url": public_url,
                "message": "TikTok 内容列表已返回精确标题和公开链接",
            }
        return {
            "status": "processing",
            "message": "TikTok 已接受提交，公开链接仍在生成",
        }

    async def add_title_tags(self, page):

        editor_locator = self.locator_base.locator('div.public-DraftEditor-content')
        await editor_locator.wait_for(state="visible", timeout=60000)
        await self.dismiss_joyride_overlay(page)
        try:
            await editor_locator.click(timeout=10000)
        except PlaywrightTimeoutError:
            # React Joyride is mounted asynchronously and can appear after the
            # initial interstitial check. Dismiss it, then retry the editor once.
            if not await self.dismiss_joyride_overlay(page):
                raise
            await editor_locator.click(timeout=10000)

        await page.keyboard.press("End")

        await page.keyboard.press("Control+A")

        await page.keyboard.press("Delete")

        await page.keyboard.press("End")

        await page.wait_for_timeout(1000)  # 等待1秒

        await page.keyboard.insert_text(self.title)
        await page.wait_for_timeout(1000)  # 等待1秒
        await page.keyboard.press("End")

        await page.keyboard.press("Enter")

        # tag part
        for index, tag in enumerate(self.tags, start=1):
            tiktok_logger.info("Setting the %s tag" % index)
            await page.keyboard.press("End")
            await page.wait_for_timeout(1000)  # 等待1秒
            await page.keyboard.insert_text("#" + tag + " ")
            await page.keyboard.press("Space")
            await page.wait_for_timeout(1000)  # 等待1秒

            await page.keyboard.press("Backspace")
            await page.keyboard.press("End")

    async def dismiss_upload_interstitials(self, page):
        """Close known onboarding/settings prompts without touching publish controls."""
        prompts = (
            ("开启自动内容检查？", "取消"),
            ("全新编辑功能已上线", "知道了"),
        )
        for prompt, button_name in prompts:
            dialog = page.locator('[role="dialog"]:visible').filter(
                has_text=prompt
            ).first
            if not await dialog.count():
                continue
            button = dialog.get_by_role("button", name=button_name, exact=True).first
            if await button.count() and await button.is_visible():
                await button.click()
                await page.wait_for_timeout(400)

        await self.dismiss_joyride_overlay(page)

    async def dismiss_joyride_overlay(self, page):
        """Dismiss upload-page tours while staying inside the Joyride portal."""
        portal = page.locator("#react-joyride-portal").first
        active_tour = page.locator(
            '#react-joyride-portal [data-test-id="overlay"]:visible, '
            '#react-joyride-portal .react-joyride__tooltip:visible'
        ).first
        if not await portal.count() or not await active_tour.count():
            return False

        tiktok_logger.info("[+] TikTok onboarding overlay detected")
        dismiss_selector = (
            '[data-action="skip"], [data-action="close"], '
            'button[aria-label="Skip"], button[aria-label="Close"], '
            'button:has-text("跳过"), button:has-text("关闭"), '
            'button:has-text("知道了"), button:has-text("Got it")'
        )
        advance_selector = (
            '[data-action="primary"], button:has-text("Next"), '
            'button:has-text("下一步"), button:has-text("完成")'
        )

        for _ in range(8):
            if not await active_tour.count() or not await active_tour.is_visible():
                return True
            dismiss_button = portal.locator(dismiss_selector).first
            if await dismiss_button.count() and await dismiss_button.is_visible():
                await dismiss_button.click(force=True)
                await page.wait_for_timeout(400)
                continue
            advance_button = portal.locator(advance_selector).first
            if await advance_button.count() and await advance_button.is_visible():
                await advance_button.click(force=True)
                await page.wait_for_timeout(400)
                continue
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(400)
            except Exception:
                pass
            break

        if await active_tour.count() and await active_tour.is_visible():
            raise RuntimeError(
                "TikTok onboarding overlay could not be dismissed before editing"
            )
        return True

    async def upload_thumbnails(self, page):
        await self.locator_base.locator(".cover-container").click()
        await self.locator_base.locator(".cover-edit-container >> text=Upload cover").click()
        async with page.expect_file_chooser() as fc_info:
            await self.locator_base.locator(".upload-image-upload-area").click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(self.thumbnail_path)
        await self.locator_base.locator('div.cover-edit-panel:not(.hide-panel)').get_by_role(
            "button", name="Confirm").click()
        await page.wait_for_timeout(3000)  # wait 3s, fix it later

    async def change_language(self, page):
        # set the language to english
        navigation_error = None
        for attempt in range(1, 4):
            try:
                await page.goto(
                    "https://www.tiktok.com",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                navigation_error = None
                break
            except Exception as exc:
                navigation_error = exc
                tiktok_logger.warning(
                    f"[+] TikTok home connection retry {attempt}/3"
                )
                await page.wait_for_timeout(2000)
        if navigation_error is not None:
            raise RuntimeError(
                "TikTok home could not be reached after 3 attempts"
            ) from navigation_error
        await page.wait_for_load_state('domcontentloaded')
        await page.wait_for_selector('[data-e2e="nav-more-menu"]')
        # 已经设置为英文, 省略这个步骤
        if await page.locator('[data-e2e="nav-more-menu"]').text_content() == "More":
            return

        await page.locator('[data-e2e="nav-more-menu"]').click()
        await page.locator('[data-e2e="language-select"]').click()
        await page.locator('#creator-tools-selection-menu-header >> text=English (US)').click()

    async def click_publish(self, page):
        publish_button = self.locator_base.locator(
            'div.button-group > button:has-text("Post"), '
            'div.button-group > button:has-text("发布")'
        ).first
        await publish_button.wait_for(state="visible", timeout=10000)
        if await publish_button.get_attribute("disabled") is not None:
            raise RuntimeError("TikTok Post button is disabled before submission")
        before_path = str(Path(self.account_file).with_name("tiktok_before_post.png"))
        try:
            await page.screenshot(full_page=True, path=before_path)
        except Exception as exc:
            tiktok_logger.warning(f"TikTok pre-submit screenshot failed: {exc}")
        await publish_button.click()
        tiktok_logger.info("  [-] Post clicked once; waiting for a definitive result")
        await self.confirm_incomplete_copyright_check(page)
        try:
            await page.wait_for_url(
                TIKTOK_CONTENT_URL_PATTERN,
                timeout=30000,
            )
        except Exception as exc:
            after_path = str(
                Path(self.account_file).with_name("tiktok_after_post_unknown.png")
            )
            try:
                await page.screenshot(full_page=True, path=after_path)
            except Exception as screenshot_exc:
                tiktok_logger.warning(
                    f"TikTok post-submit screenshot failed: {screenshot_exc}"
                )
            diagnostic_texts = await page.locator(
                '[role="alert"]:visible, [role="dialog"]:visible, '
                '[class*="toast"]:visible, [class*="error"]:visible'
            ).all_inner_texts()
            diagnostic = " | ".join(
                dict.fromkeys(
                    text.strip().replace("\n", " ")[:240]
                    for text in diagnostic_texts
                    if text.strip()
                )
            )
            detail = f"; page feedback: {diagnostic}" if diagnostic else ""
            raise RuntimeError(
                "TikTok final state is unknown after one Post click; "
                f"automatic retry was disabled{detail}"
            ) from exc
        tiktok_logger.success("  [-] video published success")

    async def confirm_incomplete_copyright_check(self, page):
        """Confirm only TikTok's known unfinished copyright-check prompt once."""
        for _ in range(20):
            if "/tiktokstudio/content" in str(page.url or ""):
                return False
            dialog = page.locator('[role="dialog"]:visible').first
            if await dialog.count() and await dialog.is_visible():
                dialog_text = (await dialog.inner_text()).strip()
                known_prompt = any(
                    marker in dialog_text
                    for marker in (
                        "版权检查未完成",
                        "版权检查尚未完成",
                        "Copyright check isn't complete",
                        "Copyright check is not complete",
                    )
                )
                if not known_prompt:
                    return False
                confirm_button = dialog.get_by_role(
                    "button",
                    name=re.compile(r"^(立即发布|Post now)$", re.IGNORECASE),
                ).first
                if not await confirm_button.count() or not await confirm_button.is_visible():
                    raise RuntimeError(
                        "TikTok copyright-check prompt appeared without its confirm button"
                    )
                await confirm_button.click()
                tiktok_logger.info(
                    "  [-] Copyright check was incomplete; clicked its final confirmation once"
                )
                return True
            await page.wait_for_timeout(500)
        return False

    async def get_last_video_id(self, page):
        public_url = await self.get_published_video_url(page)
        match = re.search(r"/video/(\d+)", public_url or "")
        return match.group(1) if match else None

    async def get_published_video_url(self, page):
        """Read the exact-title public link from TikTok Studio's current list."""
        link = page.locator('a[href*="/video/"]').filter(has_text=self.title).first
        try:
            await link.wait_for(state="visible", timeout=45000)
        except PlaywrightTimeoutError:
            return ""
        href = str(await link.get_attribute("href") or "").strip()
        if href.startswith("/"):
            href = f"https://www.tiktok.com{href}"
        if not href.startswith("https://www.tiktok.com/") or "/video/" not in href:
            return ""
        return href


    async def detect_upload_status(self, page):
        # The upload form can be localized, and a missing Post button must not
        # be interpreted as "ready". Poll for a visible, enabled button with a
        # bounded timeout so a worker cannot spin forever.
        for _ in range(300):
            try:
                post_button = self.locator_base.locator(
                    'div.button-group > button:has-text("Post"), '
                    'div.button-group > button:has-text("发布")'
                ).first
                if await post_button.count() and await post_button.is_visible():
                    if await post_button.get_attribute("disabled") is None:
                        tiktok_logger.info("  [-]video uploaded.")
                        return
                if await self.locator_base.locator(
                    'button[aria-label="Select file"], button:has-text("选择文件")'
                ).count():
                    tiktok_logger.info("  [-] found some error while uploading now retry...")
                    await self.handle_upload_error(page)
                else:
                    tiktok_logger.info("  [-] video uploading...")
                    await asyncio.sleep(2)
            except Exception:
                tiktok_logger.info("  [-] video uploading...")
                await asyncio.sleep(2)
        raise RuntimeError("TikTok video upload did not reach a ready state within 10 minutes")

    async def choose_base_locator(self, page):
        # await page.wait_for_selector('div.upload-container')
        if await page.locator('iframe[data-tt="Upload_index_iframe"]').count():
            self.locator_base = page.frame_locator(Tk_Locator.tk_iframe)
        else:
            self.locator_base = page.locator(Tk_Locator.default) 

    async def main(self):
        async with async_playwright() as playwright:
            return await self.upload(playwright)
