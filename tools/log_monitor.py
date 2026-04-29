"""
Live core game-state monitor.

Run this while Arena is open to inspect the pure log-derived snapshot that feeds
the decision engine.

Usage:
    python tools/log_monitor.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from game_state import GameStateManager
from main import load_config


def main() -> None:
    print("Arena Log Monitor -> waiting for core game state...")
    manager = GameStateManager(load_config())
    last = None

    while True:
        snapshot = manager.refresh()
        data = snapshot.to_dict()
        if data != last:
            print("\033[2J\033[H", end="")
            print(json.dumps(data, indent=2))
            last = data
        time.sleep(0.5)


if __name__ == "__main__":
    main()
