from __future__ import annotations
import time
import sys
from pathlib import Path
from loguru import logger

from src.capture.screen import ScreenCapture
from src.vision.detector import VisionDetector
from src.game_state.log_parser import ArenaLogParser
from src.game_state.grp_db import GrpDatabase
from src.game_state.match import MatchStateMachine, MatchStatus
from src.game_state.state import GameState
from src.engine.decision import DecisionEngine
from src.engine.actions import Action
from src.input.controller import InputController


def _configure_logging(cfg: dict) -> None:
    log_cfg = cfg.get("logging", {})
    logger.remove()
    logger.add(sys.stderr, level=log_cfg.get("level", "INFO"))
    log_file = log_cfg.get("file", "logs/bot.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logger.add(log_file, rotation=log_cfg.get("rotation", "10 MB"), level="DEBUG")


class Bot:
    """
    Main bot loop.

    Game state comes from the Arena log file (authoritative, structured JSON).
    Button positions come from screen capture + template matching.
    Actions are executed via PyAutoGUI.
    """

    def __init__(self, config: dict):
        _configure_logging(config)
        arena_cfg = config.get("arena", {})
        vision_cfg = config.get("vision", {})
        engine_cfg = config.get("engine", {})

        ref_res = tuple(arena_cfg.get("reference_resolution", [2560, 1440]))
        self.poll_interval = arena_cfg.get("poll_interval", 0.5)
        self.debug_screenshots = vision_cfg.get("debug_screenshots", False)
        self.debug_dir = vision_cfg.get("debug_output_dir", "captures/")

        grp_db = GrpDatabase()
        self.capture = ScreenCapture()
        self.detector = VisionDetector(
            reference_resolution=ref_res,
            threshold=vision_cfg.get("template_threshold", 0.80),
        )
        self.log_parser = ArenaLogParser(grp_db=grp_db)
        self.match_fsm = MatchStateMachine()
        self.engine = DecisionEngine(aggression=engine_cfg.get("aggression", 0.7))
        self.controller = InputController(
            action_delay=arena_cfg.get("action_delay", 0.8)
        )

        self._state: GameState = GameState()
        self._iteration = 0

    def run(self) -> None:
        logger.info("Bot started. Press Ctrl+C to stop.")
        try:
            while True:
                self._tick()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("Bot stopped.")

    def _tick(self) -> None:
        self._iteration += 1

        # 1. Pull new game state from log
        updated = self.log_parser.poll()
        if updated is not None:
            self._state = updated

        # 2. Overlay button positions from screen
        frame = self.capture.grab()
        if self.debug_screenshots:
            Path(self.debug_dir).mkdir(parents=True, exist_ok=True)
            annotated = self.detector.annotate_debug(frame)
            self.capture.save_debug(
                annotated, f"{self.debug_dir}/frame_{self._iteration:06d}.png"
            )
        buttons = self.detector.detect_buttons(frame)
        self._state.pass_button_visible,    self._state.pass_button_pos    = buttons["pass"]
        self._state.ok_button_visible,      self._state.ok_button_pos      = buttons["ok"]
        self._state.keep_hand_button_visible, self._state.keep_hand_button_pos = buttons["keep_hand"]
        self._state.mulligan_button_visible,  self._state.mulligan_button_pos  = buttons["mulligan"]

        # 3. Update match lifecycle
        ctx = self.match_fsm.update(self._state)

        # 4. Don't act while idle/searching — wait for a game
        if ctx.status in (MatchStatus.IDLE, MatchStatus.SEARCHING, MatchStatus.GAME_OVER):
            return

        # 5. Decide and act
        action: Action | None = self.engine.decide(self._state, ctx)
        if action:
            if action.type.name == "MULLIGAN":
                self.match_fsm.record_mulligan()
            self.controller.execute(action)
