from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import yaml

from clicker_agent import ExecutionHandler, ExecutionStatus
from decision_engine import DecisionEngine
from game_state import GameStateManager
from src.arena_process import ArenaProcess
from src.overlay import Overlay

LOGGER = logging.getLogger(__name__)


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    base = Path("config/settings.yaml")
    config = yaml.safe_load(base.read_text()) if base.exists() else {}
    local = Path("config/settings.local.yaml")
    if local.exists():
        local_config = yaml.safe_load(local.read_text()) or {}
        config = _deep_merge(config, local_config)
    return config


def configure_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    level_name = str(log_cfg.get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    log_file = Path(log_cfg.get("file", "logs/bot.log"))
    log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def _make_arena(config: dict) -> ArenaProcess:
    arena_cfg = config.get("arena", {})
    return ArenaProcess(
        exe_path=arena_cfg.get("exe_path", ArenaProcess.__init__.__defaults__[0]),
        startup_timeout=arena_cfg.get("startup_timeout", 120),
    )


def _is_idle_at_home(snapshot: GameSnapshot) -> bool:
    return (
        snapshot.phase == "UNKNOWN"
        and not snapshot.mulligan_pending
        and not snapshot.discard_required
        and not snapshot.has_priority
        and not snapshot.we.hand
        and not snapshot.we.battlefield
        and not snapshot.opponent.battlefield
        and not snapshot.available_action_types
    )


def cmd_run(args: argparse.Namespace, config: dict) -> None:
    arena_cfg = config.get("arena", {})
    manage = args.launch or arena_cfg.get("manage_lifecycle", False)
    if args.no_launch:
        manage = False

    arena = _make_arena(config)
    if manage:
        arena.launch()

    poll_interval = arena_cfg.get("poll_interval", 0.5)
    overlay_enabled = config.get("vision", {}).get("debug_overlay", False)
    state_manager = GameStateManager(config)
    decision_engine = DecisionEngine(aggression=config.get("engine", {}).get("aggression", 0.7))
    overlay = Overlay() if overlay_enabled else None
    executor = ExecutionHandler(
        config=config,
        state_manager=state_manager,
        action_delay=arena_cfg.get("action_delay", 0.8),
        pre_click_delay=arena_cfg.get("pre_click_delay", 0.5 if overlay_enabled else 0.0),
        verification_timeout=arena_cfg.get("verification_timeout", 5.0),
        verification_poll_interval=arena_cfg.get("verification_poll_interval", 0.25),
        overlay=overlay,
    )
    max_consecutive_failures = config.get("engine", {}).get("max_consecutive_failures", 3)
    consecutive_failure_key = ""
    consecutive_failure_count = 0

    LOGGER.info("Starting MTG Arena bot loop")
    last_wait_log = 0.0
    last_wait_message = ""
    try:
        while True:
            snapshot = state_manager.refresh()
            if not snapshot.arena_running:
                LOGGER.debug("Arena not running; skipping tick")
                time.sleep(poll_interval)
                continue

            plan = decision_engine.decide(snapshot)
            if plan is None:
                now = time.time()
                if _is_idle_at_home(snapshot):
                    wait_message = "Arena is open; waiting for a game to start"
                else:
                    wait_message = (
                        f"Waiting for priority: phase={snapshot.phase} "
                        f"turn={snapshot.turn_number} "
                        f"our_turn={snapshot.is_our_turn}"
                    )

                if wait_message != last_wait_message or now - last_wait_log >= 10:
                    LOGGER.info(wait_message)
                    last_wait_message = wait_message
                    last_wait_log = now
                time.sleep(poll_interval)
                continue

            last_wait_message = ""
            context = executor.capture_context(snapshot)
            result = executor.execute(plan, state=snapshot, context=context)
            decision_engine.record_result(plan, result.status == ExecutionStatus.SUCCESS)
            if result.status == ExecutionStatus.FAILURE:
                LOGGER.debug("Action verification failed: %s", result.reason)
                failure_key = f"{plan.action_type.value}:{plan.description}:{plan.subject}:{plan.target}"
                if failure_key == consecutive_failure_key:
                    consecutive_failure_count += 1
                else:
                    consecutive_failure_key = failure_key
                    consecutive_failure_count = 1

                if consecutive_failure_count >= max_consecutive_failures:
                    LOGGER.error(
                        "Stopping after %s consecutive failures for %s: %s",
                        consecutive_failure_count,
                        plan.description or plan.action_type.value,
                        result.reason,
                    )
                    break
            else:
                consecutive_failure_key = ""
                consecutive_failure_count = 0
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        LOGGER.info("Bot stopped by user")
    finally:
        if overlay is not None:
            overlay.stop()
        if manage:
            arena.kill()


def cmd_launch(args: argparse.Namespace, config: dict) -> None:
    _make_arena(config).launch()


def cmd_kill(args: argparse.Namespace, config: dict) -> None:
    _make_arena(config).kill()


def cmd_status(args: argparse.Namespace, config: dict) -> None:
    running = _make_arena(config).is_running()
    LOGGER.info("Arena is %s", "RUNNING" if running else "NOT running")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mtga-bot", description="MTG Arena bot")
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Run the orchestrator loop")
    launch_group = run_parser.add_mutually_exclusive_group()
    launch_group.add_argument("--launch", action="store_true", help="Launch Arena before running")
    launch_group.add_argument("--no-launch", action="store_true", help="Do not manage Arena lifecycle")

    sub.add_parser("launch", help="Launch Arena and wait for the home screen")
    sub.add_parser("kill", help="Kill the Arena process")
    sub.add_parser("status", help="Report whether Arena is running")

    args = parser.parse_args()
    if args.command is None:
        args.command = "run"
        args.launch = False
        args.no_launch = False
    return args


if __name__ == "__main__":
    parsed_args = parse_args()
    loaded_config = load_config()
    configure_logging(loaded_config)
    dispatch = {
        "run": cmd_run,
        "launch": cmd_launch,
        "kill": cmd_kill,
        "status": cmd_status,
    }
    dispatch[parsed_args.command](parsed_args, loaded_config)
