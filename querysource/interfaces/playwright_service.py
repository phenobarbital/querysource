import asyncio
import random
from abc import ABC
from typing import Optional, Dict, Any

from bs4 import BeautifulSoup
from lxml import html, etree

# Playwright Imports
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
    Browser,
    Page,
    BrowserContext,
)

# Import your logging, config, and exceptions as needed:
from navconfig.logging import logging
from ..conf import (
    OXYLABS_USERNAME,
    OXYLABS_PASSWORD,
    OXYLABS_ENDPOINT,
    GOOGLE_SEARCH_ENGINE_ID,
)
from ..exceptions import NotSupported, TimeOutError, ComponentError
from .http import ua, mobile_ua

# A list of mobile device names (these should match keys from playwright.devices)
mobile_devices = [
    "iPhone X",
    "Google Nexus 7",
    "Pixel 2",
    "Samsung Galaxy Tab",
    "Nexus 5",
]


class PlaywrightService(ABC):
    """
    PlaywrightService

    An interface for making HTTP connections using Playwright,
    analogous to your SeleniumService.
    """
    accept: str = (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    )

    def __init__(self, *args, **kwargs):
        # Configuration options
        self.accept_cookies: Optional[tuple] = kwargs.pop("accept_cookies", None)
        self.as_mobile: bool = kwargs.pop("as_mobile", False)
        self.use_proxy: bool = kwargs.pop("use_proxy", False)
        self.mobile_device: str = kwargs.pop("mobile_device", random.choice(mobile_devices))
        self.default_tag: str = kwargs.pop("default_tag", "body")
        self.accept_is_clickable: bool = kwargs.pop("accept_is_clickable", False)
        self.timeout: int = kwargs.pop("timeout", 60)
        self.wait_until: Optional[tuple] = kwargs.pop("wait_until", None)
        self.inner_tag: Optional[tuple] = kwargs.pop("inner_tag", None)

        # Headers and cookies
        self.headers: Dict[str, str] = {
            "Accept": self.accept,
            "User-Agent": random.choice(ua),
            **kwargs.get("headers", {}),
        }
        self.cookies: Dict[str, str] = kwargs.get("cookies", {})
        if isinstance(self.cookies, str):
            self.cookies = self.parse_cookies(self.cookies)

        # Playwright-related attributes
        self._playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        # Convert any element selectors provided as tuples into Playwright-friendly selectors.
        if self.accept_cookies:
            self.accept_cookies = self.check_by_attribute(self.accept_cookies)
        if self.inner_tag:
            self.inner_tag = self.check_by_attribute(self.inner_tag)

        self.logger = logging.getLogger(__name__)

    def parse_cookies(self, cookie_pair: str) -> dict:
        """Parse a cookie string into a dictionary."""
        cookies = {}
        cookie_pairs = [c.strip() for c in cookie_pair.strip().split(";") if c.strip()]
        for pair in cookie_pairs:
            if "=" in pair:
                name, value = pair.split("=", 1)
                cookies[name.strip()] = value.strip().strip('"')
        return cookies

    def check_by_attribute(self, attribute: tuple) -> str:
        """
        Convert a tuple (attribute, value) into a Playwright selector.
        For example, ('id', 'submit') becomes '#submit' and
        ('xpath', '//button') becomes 'xpath=//button'.
        """
        if not attribute:
            return ""
        el, value = attribute
        if el == "id":
            return f"#{value}"
        elif el in ("class", "class name"):
            return f".{value}"
        elif el == "name":
            return f"[name='{value}']"
        elif el == "xpath":
            return f"xpath={value}"
        elif el in ("css", "css selector"):
            return value
        elif el in ("tag", "tag name", "tagname", "tag_name"):
            return value
        else:
            raise NotSupported(f"Playwright: Attribute {el} is not supported.")

    def proxy_playwright(self, user: str, password: str, endpoint: str) -> dict:
        """Return a proxy configuration dictionary for Playwright."""
        proxy_server = f"http://{user}:{password}@{endpoint}"
        self.logger.debug(f"Using proxy: {proxy_server}")
        return {"server": proxy_server}

    async def get_driver(self) -> Page:
        """
        Initialize Playwright, launch a browser (with proxy and/or mobile emulation if configured),
        and return a new Page instance.
        """
        self._playwright = await async_playwright().start()
        browser_args = {}
        if self.use_proxy:
            proxy_config = self.proxy_playwright(
                f"customer-{OXYLABS_USERNAME}-sesstime-1", OXYLABS_PASSWORD, OXYLABS_ENDPOINT
            )
            browser_args["proxy"] = proxy_config

        # Launch Chromium in headless mode (change to headless=False for debugging)
        self.browser = await self._playwright.chromium.launch(headless=True, **browser_args)

        # Set up context options (viewport, user agent, extra headers)
        context_args: Dict[str, Any] = {
            "viewport": {"width": 1280, "height": 720},
            "user_agent": self.headers.get("User-Agent"),
            "extra_http_headers": self.headers,
        }
        if self.as_mobile:
            try:
                # Use a built‐in device preset from Playwright.
                device = self._playwright.devices[self.mobile_device]
            except KeyError:
                self.logger.warning(
                    f"Device {self.mobile_device} not found. Falling back to 'iPhone X'."
                )
                device = self._playwright.devices.get("iPhone X")
            if device:
                context_args.update(device)

        self.context = await self.browser.new_context(**context_args)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(self.timeout * 1000)  # timeout is in milliseconds
        return self.page

    async def close_driver(self):
        """Close the page, browser context, browser, and stop Playwright."""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _execute_scroll(self):
        """
        Scroll to the bottom of the page (and then back to the top) to ensure
        that lazy-loaded content is loaded.
        """
        await self.page.wait_for_load_state("load")
        await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(2)  # Give time for content to load
        await self.page.evaluate("window.scrollTo(0, 0)")

    async def save_screenshot(self, filename: str) -> None:
        """
        Save a screenshot of the entire page or of a specific element if configured.
        """
        if not self.page:
            raise ComponentError("No page available for screenshot.")

        # If you have defined a 'screenshot' attribute with a 'portion' selector,
        # capture that element only.
        if hasattr(self, "screenshot") and "portion" in getattr(self, "screenshot"):
            selector = self.check_by_attribute(self.screenshot["portion"])
            element = self.page.locator(selector)
            # Wait for the element to be visible (with nonzero dimensions)
            await element.wait_for(state="visible", timeout=20000)
            await element.screenshot(path=filename)
        else:
            # Full-page screenshot
            await self.page.screenshot(path=filename, full_page=True)

    async def get_soup(self, parser: str = "html.parser") -> BeautifulSoup:
        """
        Return a BeautifulSoup object for the current page content.
        """
        if not self.page:
            raise ComponentError("No page available to retrieve content.")
        content = await self.page.content()
        return BeautifulSoup(content, parser)

    async def get_etree(self) -> tuple:
        """
        Return a tuple of lxml etree objects (using etree.fromstring and html.fromstring)
        for the current page content.
        """
        if not self.page:
            raise ComponentError("No page available to retrieve content.")
        content = await self.page.content()
        try:
            x = etree.fromstring(content)
        except etree.XMLSyntaxError:
            x = None
        try:
            h = html.fromstring(content)
        except etree.XMLSyntaxError:
            h = None
        return x, h

    async def get_page(
        self,
        url: str,
        cookies: Optional[Dict[str, str]] = None,
        retries: int = 3,
        backoff_delay: int = 2,
    ):
        """
        Navigate to a given URL, optionally set cookies, wait for page load,
        and optionally handle any cookie-acceptance banners.
        """
        if not self.page:
            await self.get_driver()
        attempt = 0
        while attempt < retries:
            try:
                await self.page.goto(url, wait_until="load")
                if cookies:
                    # Convert your cookies to the format expected by Playwright.
                    cookie_list = []
                    for name, value in cookies.items():
                        cookie_list.append({
                            "name": name,
                            "value": value,
                            "url": url,
                        })
                    await self.context.add_cookies(cookie_list)
                    # Reload to ensure cookies take effect.
                    await self.page.reload(wait_until="load")

                # Wait for a specific element if configured
                if self.wait_until:
                    selector = self.check_by_attribute(self.wait_until)
                    await self.page.wait_for_selector(selector, timeout=20000)
                else:
                    await self.page.wait_for_selector(self.default_tag, timeout=20000)

                # Handle "Accept Cookies" if needed.
                if self.accept_cookies:
                    try:
                        if self.accept_is_clickable:
                            await self.page.wait_for_selector(self.accept_cookies, state="visible", timeout=10000)
                            await self.page.click(self.accept_cookies)
                        else:
                            await self.page.wait_for_selector(self.accept_cookies, timeout=10000)
                            await self.page.eval_on_selector(self.accept_cookies, "el => el.click()")
                    except PlaywrightTimeoutError:
                        self.logger.warning("Accept Cookies button not found.")
                await self._execute_scroll()
                return  # Exit after a successful navigation
            except PlaywrightTimeoutError:
                self.logger.warning(
                    f"Timeout occurred on attempt {attempt + 1}/{retries} for URL: {url}"
                )
                attempt += 1
                if attempt < retries:
                    await asyncio.sleep(backoff_delay)
                else:
                    raise TimeOutError(f"Timeout Error on URL {url} after {retries} attempts")
            except Exception as exc:
                raise ComponentError(f"Error during page navigation: {exc}")

    async def search_google_cse(self, query: str, max_results: int = 5) -> list:
        """
        Perform a Google Custom Search Engine (CSE) search using Playwright.
        Returns a list of results where each result is a dict with 'title' and 'link'.
        """
        search_url = f"https://cse.google.com/cse?cx={GOOGLE_SEARCH_ENGINE_ID}#gsc.tab=0&gsc.q={query}&gsc.sort="
        try:
            if not self.page:
                await self.get_driver()
            await self.page.goto(search_url, wait_until="load")
            # Wait for the search results container to appear.
            try:
                await self.page.wait_for_selector(".gsc-results", timeout=5000)
            except PlaywrightTimeoutError:
                try:
                    await self.page.wait_for_selector(".gs-no-results-result", timeout=3000)
                    return []  # No results found
                except PlaywrightTimeoutError:
                    raise RuntimeError("CSE: No results found or page failed to load.")

            # Allow any JS to finish
            await asyncio.sleep(2)
            results = []
            search_results = await self.page.query_selector_all(".gsc-webResult")
            if not search_results:
                search_results = await self.page.query_selector_all(".gsc-expansionArea")
            for result in search_results[:max_results]:
                try:
                    title_element = await result.query_selector(".gs-title")
                    if title_element:
                        title = (await title_element.inner_text()).strip()
                        url_element = await title_element.query_selector("a")
                        if url_element:
                            url = (await url_element.get_attribute("href")).strip()
                            if title and url:
                                results.append({"title": title, "link": url})
                except Exception:
                    continue  # Skip this result if any errors occur
            return results

        except PlaywrightTimeoutError as e:
            raise RuntimeError(f"CSE Timeout: {e}")
        except Exception as e:
            raise RuntimeError(f"CSE Unexpected Error: {e}")
        finally:
            await self.close_driver()
