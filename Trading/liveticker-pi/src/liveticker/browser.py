"""Slim Selenium wrapper — the collector's whole browser surface.

Replaces the app's tradinglib.web_tools: that module is written for interactive
scraping and drags along helpers this collector never uses. Everything here is
what the scraper actually calls, plus the Raspberry-Pi-relevant Chrome flags.
"""

import logging
import os
import subprocess
import sys
import tempfile

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

logger = logging.getLogger(__name__)

# Where the persistent browser profiles live. A throwaway profile means the
# consent wall returns on every start, so the cookies are worth keeping — but
# NOT under /tmp: on Raspberry Pi OS that is frequently a tmpfs, which would
# throw the profile away on every reboot. XDG data dir it is.
def default_profile_root():
    """Return the directory that holds the persistent browser profiles."""
    base = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'),
                                                           '.local', 'share')
    return os.path.join(base, 'liveticker', 'profiles')

BASE_ARGUMENTS = (
    "--disable-extensions",
    "--ignore-certificate-errors",
    "--window-size=1280,1024",
    "--start-maximized",
    "--disable-gpu",
    "--no-sandbox",              # required when running as root / in a container
    "--disable-dev-shm-usage",   # /dev/shm is tiny on a Pi -> use /tmp instead
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-session-crashed-bubble",
    "--disable-notifications",
)

# Chrome's own permission bubbles are not part of the DOM, so Selenium could
# never dismiss them. 2 = block.
BLOCKED_PERMISSIONS = {
    "profile.default_content_setting_values.notifications": 2,
    "profile.default_content_setting_values.geolocation": 2,
    "profile.default_content_setting_values.media_stream_mic": 2,
    "profile.default_content_setting_values.media_stream_camera": 2,
}


class Browser:
    """Owns one Chrome/Chromium instance."""

    default_timeout = 5

    def __init__(self, profile='', headless=False, binary='', driver_path='',
                 profile_root='', user_agent=''):
        """Configure the browser; call start() to launch it."""
        self.profile = profile
        self.headless = headless
        self.binary = binary
        self.driver_path = driver_path
        self.user_agent = user_agent
        self.profile_root = profile_root or default_profile_root()
        self.d = None
        self.By = By
        self.Keys = Keys
        self.def_to = self.default_timeout

    # -- lifecycle ------------------------------------------------------------

    def profile_path(self):
        """Return (and create) this instance's profile directory."""
        path = os.path.join(self.profile_root, self.profile)
        os.makedirs(path, exist_ok=True)
        return path

    def options(self, profile_path=None):
        """Build the Chrome options for this instance."""
        options = webdriver.ChromeOptions()
        options.page_load_strategy = 'none'
        for argument in BASE_ARGUMENTS:
            options.add_argument(argument)
        if self.headless:
            # Saves ~150 MB of RAM, but beware: some sites serve headless
            # browsers a different (table-less) page. If the collector reports
            # "table missing" in headless mode, run it under xvfb-run with
            # headless = false instead — see the README.
            options.add_argument("--headless=new")
        if self.binary:
            options.binary_location = self.binary
        if self.user_agent:
            options.add_argument(f"--user-agent={self.user_agent}")
        if profile_path:
            options.add_argument(f"--user-data-dir={profile_path}")
        options.add_experimental_option("prefs", dict(BLOCKED_PERMISSIONS))
        return options

    def start(self, timeout=20):
        """Launch the browser.

        Chrome refuses to open a profile that another instance already holds
        ("DevToolsActivePort file doesn't exist"). Rather than dying, fall back
        to a throwaway profile — the collector then meets the consent wall once
        instead of not running at all.
        """
        service = None
        if self.driver_path:
            from selenium.webdriver.chrome.service import Service
            service = Service(executable_path=self.driver_path)

        path = self.profile_path() if self.profile else None
        try:
            self.d = webdriver.Chrome(options=self.options(path), service=service)
        except Exception as exc:
            if not path:
                raise
            logger.warning("could not start Chrome with the profile %s (%s) — "
                           "falling back to a throwaway profile", path, exc)
            throwaway = tempfile.mkdtemp(prefix='liveticker-profile-')
            self.d = webdriver.Chrome(options=self.options(throwaway), service=service)

        self.d.implicitly_wait(timeout)
        self.d.set_page_load_timeout(timeout)
        return self.d

    def quit(self):
        """Close the browser and release the driver process."""
        try:
            if self.d is not None:
                self.d.quit()
        except Exception:
            logger.debug("quit failed", exc_info=True)
        finally:
            self.d = None

    def kill(self):
        """Kill the driver process tree — the only cure for a hung browser."""
        pid = None
        try:
            pid = self.d.service.process.pid
        except Exception:
            logger.debug("no driver process to kill", exc_info=True)
        if not pid:
            return
        try:
            if sys.platform == 'win32':
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                               capture_output=True, timeout=20)
            else:
                os.killpg(os.getpgid(pid), 9)
            logger.warning("killed the browser process %s", pid)
        except Exception:
            logger.warning("could not kill the browser process %s", pid, exc_info=True)

    # -- basics ---------------------------------------------------------------

    def get(self, url):
        """Navigate to a URL."""
        self.d.get(url)

    def action_chains(self):
        """Return an ActionChains bound to this driver."""
        return ActionChains(self.d)
