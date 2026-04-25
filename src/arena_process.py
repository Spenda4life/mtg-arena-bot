from __future__ import annotations
import subprocess
import time
import re
from pathlib import Path
from loguru import logger

_LOG_PATH = Path.home() / "AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log"
_HOME_PATTERN = re.compile(r"toSceneName.*Home")
_EXE_DEFAULT = "C:/Program Files/Wizards of the Coast/MTGA/MTGA.exe"


class ArenaProcess:
    """
    Manages the MTG Arena process lifecycle: launch, wait-for-ready, and kill.

    Designed for unattended use — scheduled overnight grinding sessions, CI
    runs, etc.  All operations are idempotent and safe to call when Arena is
    already in the expected state.
    """

    def __init__(self, exe_path: str = _EXE_DEFAULT, startup_timeout: int = 120):
        self.exe_path = exe_path
        self.startup_timeout = startup_timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq MTGA.exe"],
            capture_output=True, text=True,
        )
        return "MTGA.exe" in result.stdout

    def launch(self) -> None:
        """Start Arena if not already running and wait until the home screen loads."""
        if self.is_running():
            logger.info("Arena is already running — skipping launch")
            self._wait_for_home()
            return

        logger.info(f"Launching Arena: {self.exe_path}")
        subprocess.Popen([self.exe_path])

        if not self._wait_for_home():
            raise TimeoutError(
                f"Arena did not reach the home screen within {self.startup_timeout}s"
            )

    def kill(self) -> None:
        """Terminate Arena. No-op if it is not running."""
        if not self.is_running():
            logger.info("Arena is not running — nothing to kill")
            return
        logger.info("Killing Arena process")
        subprocess.run(["taskkill", "/F", "/IM", "MTGA.exe"], capture_output=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _wait_for_home(self) -> bool:
        """Poll the log until the home screen scene-change appears or we time out."""
        log_size_at_start = _LOG_PATH.stat().st_size if _LOG_PATH.exists() else 0
        deadline = time.monotonic() + self.startup_timeout
        last_pos = log_size_at_start

        logger.info("Waiting for Arena home screen…")
        while time.monotonic() < deadline:
            time.sleep(2)
            if not _LOG_PATH.exists():
                continue

            # If the log shrank (rotation on new launch), read from the start
            current_size = _LOG_PATH.stat().st_size
            if current_size < last_pos:
                last_pos = 0

            with open(_LOG_PATH, encoding="utf-8", errors="replace") as fh:
                fh.seek(last_pos)
                new_text = fh.read()
                last_pos = fh.tell()

            if _HOME_PATTERN.search(new_text):
                logger.info("Arena home screen reached")
                return True

        logger.warning(f"Timed out waiting for Arena home screen ({self.startup_timeout}s)")
        return False
