"""
Live game state monitor — run this while playing Arena to see what the
bot would read from the log in real time. Useful for calibration and debugging.

Usage:
    python tools/log_monitor.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.game_state.log_parser import ArenaLogParser
from src.game_state.grp_db import GrpDatabase
from src.game_state.match import MatchStateMachine, MatchStatus


def _bar(label: str, value: int, max_val: int = 20, width: int = 20) -> str:
    filled = int(width * min(value, max_val) / max_val)
    bar = "█" * filled + "░" * (width - filled)
    return f"{label}: [{bar}] {value}"


def _fmt_card_list(cards, label: str) -> str:
    if not cards:
        return f"  {label}: (none)"
    lines = [f"  {label}:"]
    for c in cards:
        flags = []
        if c.is_tapped:
            flags.append("tapped")
        if c.is_summoning_sick:
            flags.append("sick")
        pt = f" {c.power}/{c.toughness}" if c.power is not None else ""
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"    • {c.name}{pt}{flag_str}")
    return "\n".join(lines)


def main() -> None:
    print("Arena Log Monitor — waiting for game state...")
    grp_db = GrpDatabase()
    parser = ArenaLogParser(grp_db=grp_db)
    fsm = MatchStateMachine()
    last_turn = -1

    while True:
        state = parser.poll()
        if state is None:
            time.sleep(0.5)
            continue

        ctx = fsm.update(state)

        # Only print when turn or priority changes
        if state.turn_number == last_turn and not state.has_priority:
            time.sleep(0.25)
            continue
        last_turn = state.turn_number

        print("\033[2J\033[H", end="")  # clear screen
        print("=" * 60)
        print(f"  MTG Arena Bot — Live Monitor")
        print(f"  Match: {ctx.status.name}  Game {ctx.game_number}  "
              f"W{ctx.wins}/L{ctx.losses}")
        print("=" * 60)
        print(f"  Turn {state.turn_number}  Phase: {state.phase.name}  "
              f"{'OUR TURN' if state.is_our_turn else 'their turn'}  "
              f"{'[PRIORITY]' if state.has_priority else ''}")
        print()
        print(_bar("  Our life ", state.we.life))
        print(_bar("  Opp life ", state.opponent.life))
        print()
        mana_str = " ".join(f"{v}{k}" for k, v in state.we.mana_available.items()) or "0"
        print(f"  Our mana: {mana_str}")
        print()
        print(_fmt_card_list(state.we.hand, "Hand"))
        print(_fmt_card_list(state.we.battlefield, "Our battlefield"))
        print(_fmt_card_list(state.opponent.battlefield, "Opp battlefield"))
        if state.stack:
            print(f"  Stack: {', '.join(state.stack)}")
        print()
        if state.available_action_types:
            print(f"  Legal actions: {', '.join(set(state.available_action_types))}")
        print("=" * 60)

        time.sleep(0.25)


if __name__ == "__main__":
    main()
