# MTG Arena Bot

An autonomous bot for MTG Arena (Standard BO1) built around a strict separation
between log-derived game state, Arena-agnostic decision logic, and Arena-specific
execution.

---

## How it works

```text
Arena log file -> Log parser -> Core game state -> Decision engine -> Semantic action

Arena window -> Screen capture -> Vision detector -> Execution context

Semantic action + execution context -> Clicker / execution handler -> Arena window
```

| Layer | Component | What it does |
|---|---|---|
| Core state | `GameStateManager` + `ArenaLogParser` | Reads Arena's `Player.log` and produces a serializable, coordinate-free game snapshot |
| Core state | `GrpDatabase` | Maps Arena card GRP IDs -> names, CMC, and type using the installed SQLite card DB |
| Decision | `DecisionEngine` | Pure rule-based engine: core game state in, semantic action out |
| Execution context | `ScreenCapture` + `VisionDetector` | Captures Arena-only UI facts such as buttons, discard prompts, and playable hand outlines |
| Actor | `ExecutionHandler` | Resolves semantic actions into Arena coordinates and executes clicks / keypresses |
| Lifecycle | `ArenaProcess` | Optionally launches and kills the Arena process for unattended runs |

---

## Prerequisites

- **Python 3.12+**
- **MTG Arena** installed at the default path
  (`C:\Program Files\Wizards of the Coast\MTGA\`)
- **Tesseract OCR** (for text recognition; install from
  [tesseract-ocr.github.io](https://tesseract-ocr.github.io/))
- Arena must have **Detailed Logging** enabled

---

## Setup

```bash
pip install -r requirements.txt
```

### Enable detailed logging in Arena

1. Open MTG Arena
2. Go to **Settings -> Account**
3. Enable **Detailed Logs**
4. Restart Arena

---

## Usage

### Run the bot

```bash
python main.py
# or explicitly
python main.py run
```

### Launch Arena, run the bot, then kill Arena

```bash
python main.py run --launch
```

### Arena process commands

```bash
python main.py launch
python main.py kill
python main.py status
```

### Configuration

Copy `config/settings.yaml` to `config/settings.local.yaml` to override
settings without changing tracked defaults.

Key options:

```yaml
arena:
  manage_lifecycle: true
  startup_timeout: 120
  poll_interval: 0.5
  action_delay: 0.8
  verification_timeout: 2.5
  verification_poll_interval: 0.25

engine:
  aggression: 0.7

logging:
  level: "INFO"
  file: "logs/bot.log"
```

---

## Developer tools

```bash
python tools/log_monitor.py
python tools/list_decks.py
python tools/capture_templates.py
python tools/download_card_data.py
```

### Tests

```bash
python -m pytest tests/ -v
```

For live-client validation, follow [TESTING_CHECKLIST.md](/C:/Users/Claude/mtg-arena-bot/TESTING_CHECKLIST.md).

---

## Project structure

```text
main.py                     Entry point and orchestrator
game_state.py               Core game-state module (log-derived snapshot only)
decision_engine.py          Arena-agnostic decision layer
clicker_agent.py            Arena-specific execution layer
config/
  settings.yaml             Default configuration
  settings.local.yaml       Local overrides (gitignored)
src/
  arena_process.py          Arena launch / kill / wait-for-ready
  capture/
    screen.py               Screen capture via MSS
  vision/
    detector.py             Template matching and visual execution hints
    layout.py               Arena coordinate mapping used by the execution layer
  game_state/
    log_parser.py           Parses Player.log -> internal game state + deck info
    grp_db.py               GRP ID -> card metadata (SQLite + Scryfall fallback)
    state.py                Internal parser-side dataclasses
tools/
  log_monitor.py            Live game-state debug monitor
  list_decks.py             Print all player decks from the log
  capture_templates.py      Helper for capturing UI button templates
  download_card_data.py     Downloads Scryfall bulk card data
tests/
  test_log_parser.py
  test_layout.py
  test_three_module_architecture.py
```

---

## Notes and limitations

- **Standard BO1 only.** The decision engine does not handle sideboarding or best-of-three game plans.
- **Rule-based, not ML.** Card interactions not explicitly coded are passed through as semantic "pass priority" actions.
- **Strict separation.** The decision engine is intentionally unaware of Arena button names, templates, and coordinates.
- **Vision is still required for execution.** Arena's log is authoritative for game rules state, but button positions, prompts, and click targets remain screen-space concerns.
- **Screen resolution matters.** Adjust `layout.*` values in `settings.yaml` if execution clicks the wrong positions.
- **Use responsibly.** Automated play may violate MTG Arena's Terms of Service. Use at your own risk.
