import sys
import argparse
import yaml
from pathlib import Path

from src.bot import Bot
from src.arena_process import ArenaProcess


def load_config() -> dict:
    base = Path("config/settings.yaml")
    cfg = yaml.safe_load(base.read_text()) if base.exists() else {}
    local = Path("config/settings.local.yaml")
    if local.exists():
        local_cfg = yaml.safe_load(local.read_text()) or {}
        cfg = _deep_merge(cfg, local_cfg)
    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _make_arena(config: dict) -> ArenaProcess:
    arena_cfg = config.get("arena", {})
    return ArenaProcess(
        exe_path=arena_cfg.get("exe_path", ArenaProcess.__init__.__defaults__[0]),
        startup_timeout=arena_cfg.get("startup_timeout", 120),
    )


def cmd_run(args: argparse.Namespace, config: dict) -> None:
    arena_cfg = config.get("arena", {})
    manage = args.launch or arena_cfg.get("manage_lifecycle", False)
    # --no-launch explicitly disables lifecycle management
    if args.no_launch:
        manage = False

    arena = _make_arena(config)

    if manage:
        arena.launch()

    try:
        bot = Bot(config)
        bot.run()
    finally:
        if manage:
            arena.kill()


def cmd_launch(args: argparse.Namespace, config: dict) -> None:
    _make_arena(config).launch()


def cmd_kill(args: argparse.Namespace, config: dict) -> None:
    _make_arena(config).kill()


def cmd_status(args: argparse.Namespace, config: dict) -> None:
    running = _make_arena(config).is_running()
    print("Arena is", "RUNNING" if running else "NOT running")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mtga-bot",
        description="MTG Arena autonomous bot",
    )
    sub = parser.add_subparsers(dest="command")

    # Default: run the bot
    run_p = sub.add_parser("run", help="Run the bot (default)")
    launch_grp = run_p.add_mutually_exclusive_group()
    launch_grp.add_argument(
        "--launch", action="store_true",
        help="Launch Arena before starting (overrides manage_lifecycle in config)",
    )
    launch_grp.add_argument(
        "--no-launch", action="store_true",
        help="Do not launch or kill Arena (overrides manage_lifecycle in config)",
    )

    sub.add_parser("launch", help="Launch Arena and wait for the home screen")
    sub.add_parser("kill",   help="Kill the Arena process")
    sub.add_parser("status", help="Report whether Arena is running")

    # Treat bare invocation as 'run'
    args = parser.parse_args()
    if args.command is None:
        args.command = "run"
        args.launch = False
        args.no_launch = False
    return args


if __name__ == "__main__":
    args = parse_args()
    config = load_config()

    dispatch = {
        "run":    cmd_run,
        "launch": cmd_launch,
        "kill":   cmd_kill,
        "status": cmd_status,
    }
    dispatch[args.command](args, config)
