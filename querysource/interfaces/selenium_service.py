from abc import ABC
from typing import Optional
from collections.abc import Callable
import random
import time
# BeautifulSoup:
from bs4 import BeautifulSoup
from lxml import html, etree
# Undetected Chrome Driver:
import undetected_chromedriver as uc
# WebDriver Support:
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.firefox import GeckoDriverManager
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from webdriver_manager.core.driver_cache import DriverCacheManager
# from selenium import webdriver
from seleniumwire import webdriver
# Selenium:
from selenium import webdriver as selenium_driver
# Selenium Proxy:
from selenium.webdriver import Proxy
# Chrome Support:
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
# Firefox Support:
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
# Edge Support:
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
# Safari Support:
from selenium.webdriver.safari.options import Options as SafariOptions
from selenium.webdriver.safari.service import Service as SafariService
# WebKitGTK Support:
from selenium.webdriver.webkitgtk.service import Service as WebKitGTKService
from selenium.webdriver.webkitgtk.options import Options as WebKitGTKOptions
# Selenium Options:
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException
)
from navconfig import BASE_DIR
from navconfig.logging import logging
from ..conf import (
    ### Oxylabs Proxy Support for Selenium
    OXYLABS_USERNAME,
    OXYLABS_PASSWORD,
    OXYLABS_ENDPOINT,
    GOOGLE_SEARCH_ENGINE_ID
)
from ..exceptions import (
    NotSupported,
    TimeOutError,
    ComponentError
)
from .http import ua, mobile_ua


logging.getLogger(name='selenium.webdriver').setLevel(logging.INFO)
logging.getLogger(name='WDM').setLevel(logging.WARNING)
logging.getLogger(name='hpack').setLevel(logging.WARNING)
logging.getLogger(name='seleniumwire').setLevel(logging.WARNING)
logging.getLogger(name='undetected_chromedriver').setLevel(logging.INFO)


mobile_devices = [
    'iPhone X',
    'Google Nexus 7',
    'Pixel 2',
    'Samsung Galaxy Tab',
    'Nexus 5',
]


class SeleniumService(ABC):
    """SeleniumService.

        Interface for making HTTP connections using Selenium.
    """
    chrome_options = [
        # "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--enable-automation",
        "--lang=en",
        "--disable-dev-shm-usage",
        "--disable-features=VizDisplayCompositor",
        "--disable-features=IsolateOrigins",
        # "--disable-extensions",
        # "--disable-features=NetworkService,NetworkServiceInProcess",
        # "--ignore-certificate-errors-spki-list",
        # "--allow-insecure-localhost",
        # "--ignore-ssl-errors",
        # "--disable-web-security",
        # "--allow-running-insecure-content",
    ]
    undetected_options = [
        "--disable-gpu",
        "--no-sandbox",
        "--enable-automation",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=NetworkService,NetworkServiceInProcess",
        "--disable-dev-shm-usage",
    ]
    firefox_options = [
        "--no-sandbox",
        "--disable-gpu",
        # browser viewport size
        "--width=1920",
        "--height=1080"
    ]
    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9"  # noqa

    def __init__(self, *args, **kwargs):
        self._driver: Callable = None
        self._wait: WebDriverWait = None
        # Accept Cookies is a tuple with button for accepting cookies.
        self.accept_cookies: tuple = kwargs.get('accept_cookies', None)
        self.use_wire: bool = kwargs.get('use_wire', False)
        self.use_firefox: bool = kwargs.get('use_firefox', False)
        self.use_edge: bool = kwargs.get('use_edge', False)
        self.use_safari: bool = kwargs.get('use_safari', False)
        self.use_webkit: bool = kwargs.get('use_webkit', False)
        self.as_mobile: bool = kwargs.get('as_mobile', False)
        self.use_undetected: bool = kwargs.get('use_undetected', False)
        self.headless: bool = kwargs.get('headless', True)
        self.enable_http2: bool = kwargs.get('enable_http2', True)
        self._browser_binary: str = kwargs.get('browser_binary', None)
        self._driver_binary: str = kwargs.get('driver_binary', None)
        self._userdata: str = kwargs.get('userdata', None)
        # Device type, defaulting to:
        # TODO: create a dictionary matching userAgent and Mobile Device.
        self.mobile_device: str = kwargs.get(
            'mobile_device', 'Pixel 2'
        )
        self.default_tag: str = kwargs.get('default_tag', 'body')
        self.accept_is_clickable: bool = kwargs.get('accept_is_clickable', False)
        self.timeout: int = kwargs.get('timeout', 60)
        self.wait_until: tuple = kwargs.get('wait_until', None)
        self.inner_tag: tuple = kwargs.get('inner_tag', None)
        self._options = None
        super().__init__(*args, **kwargs)
        headers = kwargs.get('headers', {})
        self.headers: dict = {
            "Accept": self.accept,
            "TE": "trailers",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": random.choice(ua),
            **headers
        }
        # Configure Cookies:
        self.cookies: dict = kwargs.get('cookies', {})
        if isinstance(self.cookies, str):
            self.cookies = self.parse_cookies(self.cookies)

    def parse_cookies(self, cookie_pair: str) -> dict:
        """Parse the Cookies."""
        cookies = {}
        cookie_pairs = [c.strip() for c in cookie_pair.strip().split(';') if c.strip()]
        for pair in cookie_pairs:
            if '=' in pair:
                name, value = pair.split('=', 1)
                name = name.strip()
                value = value.strip().strip('"')  # remove quotes if any
                cookies[name] = value
        return cookies

    def check_by_attribute(self, attribute: tuple):
        if not attribute:
            return None
        el = attribute[0]
        value = attribute[1]
        new_attr = None
        if el == 'id':
            new_attr = (By.ID, value)
        elif el in ('class', 'class name'):
            new_attr = (By.CLASS_NAME, value)
        elif el == 'name':
            new_attr = (By.NAME, value)
        elif el == 'xpath':
            new_attr = (By.XPATH, value)
        elif el == 'css':
            new_attr = (By.CSS_SELECTOR, value)
        elif el in ('tag', 'tag name', 'tagname', 'tag_name'):
            new_attr = (By.TAG_NAME, value)
        else:
            raise NotSupported(
                f"Selenium: Attribute {el} is not supported."
            )
        return new_attr

    def driver(self):
        return self._driver

    def close_driver(self):
        if self._driver:
            self._driver.quit()

    async def start(self, **kwargs) -> bool:
        await super(SeleniumService, self).start(**kwargs)
        # Check the Accept Cookies:
        if self.accept_cookies:
            if not isinstance(self.accept_cookies, tuple):
                raise NotSupported(
                    "Accept Cookies must be a Tuple with the Button to Accept Cookies."
                )
            self.accept_cookies = self.check_by_attribute(self.accept_cookies)
        if self.inner_tag:
            self.inner_tag = self.check_by_attribute(self.inner_tag)
        if hasattr(self, 'screenshot'):
            try:
                self.screenshot['portion'] = self.check_by_attribute(
                    self.screenshot['portion']
                )
            except (KeyError, ValueError):
                pass
        return True

    def proxy_selenium(self, user: str, password: str, endpoint: str, only_http: bool = True) -> dict:
        if only_http is True:
            wire_options = {
                "proxy": {
                    "http": f"http://{user}:{password}@{endpoint}",
                    "https": f"http://{user}:{password}@{endpoint}",
                }
            }
        else:
            wire_options = {
                "proxy": {
                    "http": f"http://{user}:{password}@{endpoint}",
                    "https": f"https://{user}:{password}@{endpoint}",
                    # "socks5": f"https://{user}:{password}@{endpoint}",
                }
            }
        # print(':: Proxy :', wire_options)
        return wire_options

    async def get_driver(self):
        """
        Return a Selenium Driver instance for Firefox, Edge, Safari, WebKitGTK or Chrome.

        This method configures the browser based on instance flags (such as:
        - self.use_firefox, self.use_edge, self.use_safari, self.use_webkit
        - self.use_proxy, self._free_proxy, self.use_undetected
        - self.as_mobile for mobile emulation (Chrome-only)
        - self.enable_http2 (if False, HTTP/2 will be disabled)

        It applies a common set of options (stored in self.chrome_options) to all browsers
        and adds proxy settings (if configured) to the options and/or desired capabilities.

        Returns:
            A Selenium WebDriver instance.
        """
        proxies = None
        proxy = None
        # Define first which webdriver to use:
        if self.use_wire is True:
            # Use Wire protocol for Selenium
            self._webdriver = webdriver
            _options = {
                "seleniumwire_options": {
                    "proxy": None,
                    'http2': False  # Explicitly disable HTTP/2 in Selenium Wire
                }
            }
        else:
            self._webdriver = selenium_driver
            _options = {}
        # Selenium Options:
        if self.use_firefox is True:
            self._options = FirefoxOptions()
        elif self.use_edge is True:
            self._options = EdgeOptions()
        elif self.use_safari is True:
            self._options = SafariOptions()
        elif self.use_webkit is True:
            self._options = WebKitGTKOptions()
        elif self.use_undetected is True:
            # Start an undetected Chrome instance
            self._options = uc.ChromeOptions()
        else:
            # used for Chrome by default
            self._options = ChromeOptions()
        # Add UA to Headers:
        _ua = random.choice(ua)
        self._options.add_argument(f"user-agent={_ua}")
        # Configure Proxy Support
        if self.use_proxy is True:
            proxy = Proxy()
            if self._free_proxy is False:
                # Oxylabs Proxy:
                if hasattr(self, 'us_proxy'):
                    endpoint = "us-pr.oxylabs.io:10000"
                else:
                    endpoint = OXYLABS_ENDPOINT
                customer = f"customer-{OXYLABS_USERNAME}-sesstime-1"
                proxies = self.proxy_selenium(
                    customer, OXYLABS_PASSWORD, endpoint
                )
                proxy.http_proxy = f"http://{customer}:{OXYLABS_PASSWORD}@{endpoint}"
                proxy.ssl_proxy = f"https://{customer}:{OXYLABS_PASSWORD}@{endpoint}"
                proxy = {
                    "proxyType": "manual",
                    "httpProxy": proxy.http_proxy,
                    "sslProxy": proxy.ssl_proxy,
                }
                # and using the simple config:
                self._options.add_argument(
                    f"--proxy-server=http://{OXYLABS_USERNAME}:{OXYLABS_PASSWORD}@{OXYLABS_ENDPOINT}"
                )
                if self.use_wire is True:
                    _options['seleniumwire_options']['proxy'] = proxies['proxy']
            else:
                proxies = await self.get_proxies()
                # proxies is a list of IP:port, so we need to convert it to a dict
                proxy.http_proxy = f"http://{proxies[0]}"
                proxy.ssl_proxy = f"https://{proxies[0]}"
                self._options.add_argument(f"--proxy-server={proxy.http_proxy}")
                if self.use_wire is True:
                    _options['seleniumwire_options']['proxy'] = {
                        "http": f"http://{proxies[0]}",
                        "https": f"https://{proxies[0]}"
                    }
        if self.use_undetected is True:
            for option in self.undetected_options:
                try:
                    self._options.add_argument(option)
                except Exception:
                    # If the browser does not support add_argument, skip it.
                    pass
            # Start an undetected Chrome instance
            self._options.headless = self.headless  # Run in visible mode to reduce bot detection
            self._driver = uc.Chrome(
                options=self._options,
                headless=self.headless,
                use_subprocess=False,
                advanced_elements=True,
                enable_cdp_events=True
            )
        elif self.use_firefox is True:
            # Use Firefox Browser
            # if True, Run in visible mode to reduce bot detection
            self._options.headless = self.headless
            for option in self.firefox_options:
                self._options.add_argument(option)
            if self.headless is True:
                self._options.add_argument("--headless")
            self._options.set_preference("network.http.http2.enabled", self.enable_http2)
            if self.use_proxy is True:
                customer = f"customer-{OXYLABS_USERNAME}-sesstime-1"
                proxy = {
                    "proxyType": "manual",
                    "httpProxy": f"{customer}:{OXYLABS_PASSWORD}@{OXYLABS_ENDPOINT}",
                    "sslProxy": f"{customer}:{OXYLABS_PASSWORD}@{OXYLABS_ENDPOINT}",
                }
                self._options.set_capability("proxy", proxy)
            if self._browser_binary:
                self._options.binary_location = self._browser_binary
                service = FirefoxService(
                    GeckoDriverManager().install()
                )
            elif self._driver_binary:
                # Use the binary driver if available
                service = FirefoxService(
                    executable_path=self._driver_binary
                )
            else:
                # Use the cached driver if available
                cache_manager = DriverCacheManager(valid_range=7)
                service = FirefoxService(
                    GeckoDriverManager(
                        cache_manager=cache_manager
                    ).install()
                )
            self._driver = self._webdriver.Firefox(
                service=service,
                options=self._options,
                **_options
            )
        elif self.use_edge is True:
            # Use Chromium Edge Browser
            # Use Edge in headless mode to reduce bot detection
            self._options.headless = self.headless
            if self.headless is True:
                self._options.add_argument("--headless=new")
            # if self.use_proxy is True:
            #     self._options.set_capability("proxy", proxy)
            if self._browser_binary is not None:
                self._options.binary_location = self._browser_binary
                service = EdgeService(
                    executable_path=self._browser_binary
                )
            else:
                service = EdgeService(
                    EdgeChromiumDriverManager().install()
                )
            self._options.set_capability("ms:edgeOptions", {"http2": self.enable_http2})
            self._driver = self._webdriver.Edge(
                service=service,
                options=self._options,
                **_options
            )
        elif self.use_safari is True:
            # Use Safari Browser
            self._driver = self._webdriver.Safari(
                service=SafariService(
                    executable_path=self._browser_path
                ),
                options=self._options,
                **_options
            )
        elif self.use_webkit is True:
            # Use WebKitGTK Browser
            self._driver = self._webdriver.WebKitGTK(
                service=WebKitGTKService().install(),
                options=self._options,
                **_options
            )
        else:
            # Use Chrome Browser
            if self.use_proxy is True:
                self._options.set_capability("proxy", proxy)
            if self.headless is True:
                self._options.add_argument("--headless=new")
            if self._browser_binary:
                self._options.binary_location = self._browser_binary
                # self._options.add_argument(
                #     f"--user-data-dir={self.self._userdata}"
                # )
            service = ChromeService(
                ChromeDriverManager().install()
            )
            for option in self.chrome_options:
                try:
                    self._options.add_argument(option)
                except Exception:
                    # If the browser does not support add_argument, skip it.
                    pass
            if self.as_mobile is True:
                # Mobile Device:
                self.mobile_device = random.choice(mobile_devices)
                # Use Chrome mobile emulation options
                mobile_emulation_options = {
                    "deviceName": self.mobile_device,
                    "userAgent": random.choice(mobile_ua)
                }
                self._options.add_experimental_option(
                    "mobileEmulation",
                    mobile_emulation_options
                )
                self._logger.debug(
                    f"Running in mobile emulation mode as {self.mobile_device}"
                )
            # Explicitly disable HTTP/2
            if self.enable_http2 is False:
                self._options.add_experimental_option(
                    "prefs", {"disable-http2": True}
                )
            self._driver = self._webdriver.Chrome(
                service=service,
                options=self._options,
                **_options
            )
        # Creating the WebDriverWait and Return the Driver:
        self._wait = WebDriverWait(self._driver, self.timeout)
        return self._driver

    def _execute_scroll(self, scroll_pause_time=1.0, max_scrolls=5):
        """
        Execute a progressive scroll through the page to ensure dynamic content loads.

        Args:
            scroll_pause_time (float): Time to pause between scrolls
            max_scrolls (int): Maximum number of scroll operations
        """
        try:
            # Wait for the page to be loaded initially
            WebDriverWait(self._driver, 20).until(
                lambda driver: driver.execute_script("return document.body.scrollHeight") > 0
            )

            # Get initial scroll height
            last_height = self._driver.execute_script("return document.body.scrollHeight")

            # Progressive scrolling
            for scroll in range(max_scrolls):
                # Scroll down to bottom in steps
                self._driver.execute_script(f"window.scrollTo(0, {(scroll+1) * last_height/max_scrolls});")

                # Wait to load page
                time.sleep(scroll_pause_time)

                # Check if new elements have loaded after each partial scroll
                new_height = self._driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height and scroll > 0:
                    # If no new content loaded after first scroll, break
                    break

                last_height = new_height

                # If this is the last scroll, try to wait for any AJAX to complete
                if scroll == max_scrolls - 1:
                    time.sleep(scroll_pause_time * 1.5)

            # Scroll back to top for better user interaction
            self._driver.execute_script("window.scrollTo(0, 0);")
        except Exception as e:
            # Log but don't fail completely on scroll errors
            self._logger.warning(f"Error during scroll operation: {e}")

    def save_screenshot(self, filename: str) -> None:
        """Saving and Screenshot of entire Page."""
        original_size = self._driver.get_window_size()
        width = self._driver.execute_script(
            'return document.body.parentNode.scrollWidth'
        ) or 1920
        height = self._driver.execute_script(
            'return document.body.parentNode.scrollHeight'
        ) or 1080
        if not width:
            width = 1920
        if not height:
            height = 1080
        self._driver.set_window_size(width, height)
        self._execute_scroll()

        # Ensure the page is fully loaded after resizing
        self._wait.until(
            lambda driver: driver.execute_script("return document.readyState") == "complete"
        )

        # Wait for specific elements to load
        if self.wait_until:
            WebDriverWait(self._driver, 20).until(
                EC.presence_of_all_elements_located(
                    self.wait_until
                )
            )
        if 'portion' in self.screenshot:
            element = self._driver.find_element(*self.screenshot['portion'])
            # Check if the element has a size
            size = element.size
            if size['height'] == 0 or size['width'] == 0:
                # Try scrolling or waiting until element is visible
                self.logger.warning(
                    "Element to screenshot has zero dimension, waiting for it to render..."
                )
                WebDriverWait(self._driver, 20).until(
                    lambda driver: element.size['height'] > 0 and element.size['width'] > 0
                )
            element.screenshot(filename)
        else:
            # Take a full-page screenshot
            self._driver.save_screenshot(filename)
        # resize to the Original Size:
        self._driver.set_window_size(
            original_size['width'],
            original_size['height']
        )

    def get_soup(self, content: str, parser: str = 'html.parser'):
        """Get a BeautifulSoup Object."""
        return BeautifulSoup(content, parser)

    def get_etree(self, content: str) -> tuple:
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
        cookies: Optional[dict] = None,
        retries: int = 3,
        backoff_delay: int = 2
    ):
        """get_page with selenium.

        Get one page using Selenium.
        """
        if not self._driver:
            await self.get_driver()
        attempt = 0
        # Debug for using Proxy:
        # self._driver.get('https://api.ipify.org?format=json')
        # page_source = self._driver.page_source
        # print(page_source)
        while attempt < retries:
            try:
                try:
                    self._driver.delete_all_cookies()
                except Exception:
                    pass
                self._driver.get(url)
                if cookies:
                    # Add the cookies
                    for cookie_name, cookie_value in cookies.items():
                        if cookie_value:
                            self._driver.add_cookie({'name': cookie_name, 'value': cookie_value})
                        # Refresh the page to apply the cookies
                        self._driver.refresh()

                # Ensure the page is fully loaded before attempting to click
                self._wait.until(
                    lambda driver: driver.execute_script("return document.readyState") == "complete"
                )

                # Wait for specific elements to load (replace with your actual elements)
                if self.wait_until:
                    WebDriverWait(self._driver, 20).until(
                        EC.presence_of_all_elements_located(
                            self.wait_until
                        )
                    )
                else:
                    # Wait for the tag to appear in the page.
                    self._wait.until(
                        EC.presence_of_element_located(
                            (By.TAG_NAME, self.default_tag)
                        )
                    )
                # Accept Cookies if enabled.
                if self.accept_cookies:
                    # Wait for the button to appear and click it.
                    try:
                        # Wait for the "Ok" button to be clickable and then click it
                        if self.accept_is_clickable is True:
                            accept_button = self._wait.until(
                                EC.element_to_be_clickable(self.accept_cookies)
                            )
                            accept_button.click()
                        else:
                            accept_button = self._wait.until(
                                EC.presence_of_element_located(
                                    self.accept_cookies
                                )
                            )
                        self._driver.execute_script("arguments[0].click();", accept_button)
                    except TimeoutException:
                        self._logger.warning(
                            'Accept Cookies Button not found'
                        )
                # Execute an scroll of the page:
                self._execute_scroll()
                return
            except TimeoutException:
                # The page never reached complete.
                print("Page did not reach a complete readyState.")
                print("Current Page Source:")
                print('===========================')
                print(self._driver.page_source)
                print('===========================')
                # Challenge Button:
                # Try to detect the challenge element. For example, if the button has text "Pulsar y mantener pulsado"

                wait = WebDriverWait(self._driver, 20)
                base = wait.until(EC.presence_of_element_located((By.ID, "px-captcha")))
                iframe = base.find_element(By.TAG_NAME, "iframe")
                print('IFRAME > ', iframe)
                self._driver.switch_to.frame(iframe)
                challenge_button = self._driver.find_element(
                    By.XPATH, "//p[contains(text(), 'Pulsar y mantener pulsado')]"
                )
                print('BUTTON HERE > ', challenge_button)

                try:
                    challenge_button = WebDriverWait(self._driver, 5).until(
                        EC.presence_of_element_located(challenge_button)
                    )
                    print('BUTTON HERE > ', challenge_button)
                    # If we found the button, simulate the click and hold action
                    actions = ActionChains(self._driver)
                    # Hold the button for, say, 5 seconds
                    actions.click_and_hold(challenge_button).pause(5).release().perform()
                    self._driver.switch_to.default_content()
                    # Optionally wait again for the page to load after the challenge
                    self._wait.until(
                        lambda driver: driver.execute_script("return document.readyState") == "complete"
                    )
                    # Execute an scroll of the page:
                    self._execute_scroll()
                    return
                except TimeoutException:
                    # If the challenge button isn't present, continue as normal
                    pass
                attempt += 1
                if attempt < retries:
                    self._logger.warning(
                        f"TimeoutException occurred. Retrying ({attempt}/{retries}) in {backoff_delay}s..."
                    )
                    time.sleep(backoff_delay)
                else:
                    raise TimeOutError(f"Timeout Error on URL {self.url} after {retries} attempts")
            except Exception as exc:
                raise ComponentError(
                    f"Error running Scrapping Tool: {exc}"
                )

    async def search_google_cse(self, query: str, max_results: int = 5):
        """
        Search Google Custom Search Engine (CSE) using Selenium.

        Args:
            query (str): The search query.
            max_results (int, optional): Maximum number of search results to return.

        Returns:
            list[dict]: A list of search results with 'title' and 'link'.
        """
        try:
            search_url = f"https://cse.google.com/cse?cx={GOOGLE_SEARCH_ENGINE_ID}#gsc.tab=0&gsc.q={query}&gsc.sort="
            driver = await self.get_driver()
            driver.get(search_url)

            # ✅ Wait for search results or "No results" message
            try:
                WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "gsc-results"))
                )
            except TimeoutException:
                try:
                    WebDriverWait(driver, 3).until(
                        EC.presence_of_element_located((By.CLASS_NAME, "gs-no-results-result"))
                    )
                    return []  # No results found, return an empty list
                except TimeoutException:
                    raise RuntimeError("CSE: No results found or page failed to load.")

            time.sleep(2)  # Allow JS to finalize

            # ✅ Extract search results
            results = []
            try:
                search_results = driver.find_elements(By.CLASS_NAME, "gsc-webResult")
            except NoSuchElementException:
                search_results = driver.find_elements(By.CLASS_NAME, "gsc-expansionArea")

            for result in search_results[:max_results]:
                try:
                    title_element = result.find_element(By.CLASS_NAME, "gs-title")
                    url_element = title_element.find_element(By.TAG_NAME, "a") if title_element else None

                    if title_element and url_element:
                        title = title_element.text.strip()
                        url = url_element.get_attribute("href").strip()
                        if title and url:
                            results.append({"title": title, "link": url})

                except NoSuchElementException:
                    continue  # Skip missing results

            return results

        except NoSuchElementException as e:
            raise RuntimeError(f"CSE Error: Element not found ({e})")
        except TimeoutException as e:
            raise RuntimeError(f"CSE Timeout: {e}")
        except WebDriverException as e:
            raise RuntimeError(f"CSE WebDriver Error: {e}")
        except RuntimeError as e:
            if str(e) == "CSE: No results found or page failed to load.":
                return []
            raise RuntimeError(f"CSE Runtime Error: {e}")
        except Exception as e:
            raise RuntimeError(f"CSE Unexpected Error: {e}")
        finally:
            self.close_driver()  # Always close driver
