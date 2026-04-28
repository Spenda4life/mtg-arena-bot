from __future__ import annotations
import time
import sys
from pathlib import Path
from loguru import logger

from src.capture.screen import ScreenCapture, is_arena_running
from src.vision.detector import VisionDetector
from src.vision.layout import CardPositionMapper
from src.game_state.log_parser import ArenaLogParser
from src.game_state.grp_db import GrpDatabase
from src.game_state.match import MatchStateMachine, MatchStatus
from src.game_state.state import GameState
from src.engine.decision import DecisionEngine
from src.engine.actions import Action
from src.input.controller import InputController, park_cursor_if_over_hand
from src.overlay import Overlay, OverlayData
from src.navigation.navigator import NavigationEngine


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
        layout = CardPositionMapper.from_config(config)
        self.log_parser = ArenaLogParser(grp_db=grp_db, layout=layout)
        self.match_fsm = MatchStateMachine()
        self.engine = DecisionEngine(aggression=engine_cfg.get("aggression", 0.7))
        self.controller = InputController(
            action_delay=arena_cfg.get("action_delay", 0.8)
        )

        self.navigator = NavigationEngine(
            detector=self.detector,
            capture=self.capture,
            config=config,
            log_parser=self.log_parser,
        )

        self._state: GameState = GameState()
        self._iteration = 0
        self._navigating = False
        self._last_action_str = ""
        self.overlay = Overlay()

    def run(self) -> None:
        logger.info("Bot started. Press Ctrl+C to stop.")
        self._navigating = True
        if not self.navigator.navigate_to_game():
            logger.error("Navigation failed — could not start a game. Check templates and Arena state.")
            return
        self._navigating = False
        # If navigator consumed a log poll to detect in-game state, seed our _state.
        if self.navigator.initial_state is not None:
            self._state = self.navigator.initial_state
            self.navigator.initial_state = None
        try:
            while True:
                self._tick()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("Bot stopped.")

    def _tick(self) -> None:
        if not is_arena_running():
            logger.debug("Arena not running — skipping tick")
            return

        self._iteration += 1

        # 1. Pull new game state from log
        updated = self.log_parser.poll()
        if updated is not None:
            self._state = updated

        # 2. Overlay button positions from screen
        park_cursor_if_over_hand()
        frame = self.capture.grab()
        if self.debug_screenshots:
            Path(self.debug_dir).mkdir(parents=True, exist_ok=True)
            annotated = self.detector.annotate_debug(frame)
            self.capture.save_debug(
                annotated, f"{self.debug_dir}/frame_{self._iteration:06d}.png"
            )
        buttons = self.detector.detect_buttons(frame)
        self._state.pass_button_visible,      self._state.pass_button_pos      = buttons["pass"]
        self._state.keep_hand_button_visible, self._state.keep_hand_button_pos = buttons["keep_hand"]
        self._state.mulligan_button_visible,  self._state.mulligan_button_pos  = buttons["mulligan"]

        # Detect which hand cards have Arena's blue "playable" outline
        self._state.playable_hand_positions = self.detector.detect_playable_hand_cards(frame)

        # Detect discard-to-hand-size prompt
        self._state.discard_prompt_visible, self._state.discard_submit_pos = \
            self.detector.detect_discard_state(frame)

        # ok_button: use vision if template exists; otherwise infer from log state.
        # Spacebar is Arena's default action, so pressing it when priority is held
        # and the phase is unknown (pre-game prompts, triggered-ability windows) is safe.
        ok_vis, ok_pos = buttons["ok"]
        if not ok_vis and self._state.has_priority and self._state.phase.name == "UNKNOWN":
            ok_vis = True
        self._state.ok_button_visible = ok_vis
        self._state.ok_button_pos     = ok_pos

        # 3. Update match lifecycle
        ctx = self.match_fsm.update(self._state)

        # 4. Don't act while idle/searching — wait for a game
        if ctx.status in (MatchStatus.IDLE, MatchStatus.SEARCHING):
            self._push_overlay("waiting for game")
            return

        if ctx.status == MatchStatus.GAME_OVER and not self._navigating:
            self._navigating = True
            self._push_overlay("navigating to next game")
            self.navigator.handle_game_over()
            if not self.navigator.navigate_to_game():
                logger.error("Re-navigation failed — stopping bot loop")
                raise KeyboardInterrupt
            self._navigating = False
            return

        # 5. Decide and act
        action: Action | None = self.engine.decide(self._state, ctx)
        if action:
            if action.type.name == "MULLIGAN":
                self.match_fsm.record_mulligan()
            self._last_action_str = str(action)
            self.controller.execute(action)

        self._push_overlay("")

    def _push_overlay(self, status: str) -> None:
        s = self._state
        self.overlay.update(OverlayData(
            status=status,
            phase=s.phase.name,
            has_priority=s.has_priority,
            our_life=s.we.life,
            opp_life=s.opponent.life,
            hand_count=len(s.we.hand),
            playable_count=len(s.playable_hand_positions),
            last_action=self._last_action_str,
            pending=self.engine.pending_description,
        ))
