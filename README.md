# MTG Arena Bot

An autonomous bot for MTG Arena (Standard BO1) that reads game state from
Arena's log file and executes actions via keyboard and mouse automation.

---

## How it works

```
Arena log file  ──►  Log parser  ──►  Game state
                                           │
Screen capture  ──►  Vision detector  ─────┤
                                           │
                                      Decision engine
                                           │
                                      Input controller  ──►  Arena window
```

| Layer | Component | What it does |
|---|---|---|
| Game state | `ArenaLogParser` | Reads Arena's `Player.log` to extract phase, life totals, hand, battlefield, mana, available actions |
| Game state | `GrpDatabase` | Maps Arena card GRP IDs → names, CMC, type using the installed SQLite card DB |
| Game state | `MatchStateMachine` | Tracks match lifecycle (idle → mulliganing → playing → game over) |
| Vision | `ScreenCapture` + `VisionDetector` | Grabs frames and finds UI buttons (pass, OK, keep hand, mulligan) via template matching |
| Decision | `DecisionEngine` | Rule-based engine: keep 2–5 land hands, play land → cast highest CMC spell, attack all, block to trade |
| Input | `InputController` | Executes actions via PyAutoGUI (keyboard shortcuts first, clicks for card selection and targeting) |
| Lifecycle | `ArenaProcess` | Optionally launches and kills the Arena process for unattended runs |

---

## Prerequisites

- **Python 3.12+**
- **MTG Arena** installed at the default path
  (`C:\Program Files\Wizards of the Coast\MTGA\`)
- **Tesseract OCR** (for text recognition — install from
  [tesseract-ocr.github.io](https://tesseract-ocr.github.io/))
- Arena must have **Detailed Logging** enabled (one-time setup, see below)

---

## Setup

```bash
pip install -r requirements.txt
```

### Enable detailed logging in Arena (one-time)

Arena writes full game state JSON to its log only when detailed logging is on.

1. Open MTG Arena
2. Go to **Settings → Account**
3. Enable the **Detailed Logs** toggle
4. Restart Arena

Once enabled, the toggle persists across sessions.

---

## Usage

### Run the bot (Arena already open)

```bash
python main.py
# or explicitly
python main.py run
```

### Launch Arena, run the bot, then kill Arena

```bash
python main.py run --launch
```

This is the recommended mode for **scheduled / unattended grinding** — the bot
manages the full Arena lifecycle itself.

### Arena process commands

```bash
python main.py launch   # start Arena and wait for the home screen
python main.py kill     # terminate Arena
python main.py status   # print whether Arena is running
```

### Configuration

Copy `config/settings.yaml` to `config/settings.local.yaml` to override
settings without touching the tracked file.

Key options:

```yaml
arena:
  manage_lifecycle: true    # auto-launch and kill Arena (good for scheduled runs)
  startup_timeout: 120      # seconds to wait for Arena home screen
  poll_interval: 0.5        # how often (seconds) the bot reads the log
  action_delay: 0.8         # pause after each click/keypress

engine:
  aggression: 0.7           # 0 = conservative blocks, 1 = always attack

logging:
  level: "INFO"             # DEBUG for verbose output
  file: "logs/bot.log"
```

### Scheduled daily grinding (Windows Task Scheduler)

Create a task that runs:

```
python C:\path\to\mtg-arena-bot\main.py run --launch
```

With `manage_lifecycle: false` in `settings.yaml` (the `--launch` flag
overrides it for this run), the bot will launch Arena, play until interrupted
or Arena closes, then exit cleanly.

---

## Developer tools

```bash
# Live game-state monitor (run while playing Arena)
python tools/log_monitor.py

# List all your decks with full card lists
python tools/list_decks.py

# Capture button templates for vision calibration
python tools/capture_templates.py

# Download Scryfall card data (optional GRP DB fallback)
python tools/download_card_data.py
```

### Tests

```bash
python -m pytest tests/ -v
```

---

## Project structure

```
main.py                     Entry point and CLI
config/
  settings.yaml             Default configuration
  settings.local.yaml       Local overrides (gitignored)
src/
  bot.py                    Main bot loop
  arena_process.py          Arena launch / kill / wait-for-ready
  capture/
    screen.py               Screen capture via MSS
  vision/
    detector.py             Template matching for UI buttons
    layout.py               Maps card zones to pixel coordinates
  game_state/
    log_parser.py           Parses Player.log → GameState + DeckInfo
    grp_db.py               GRP ID → card metadata (SQLite + Scryfall fallback)
    match.py                Match lifecycle state machine
    state.py                GameState / PlayerState / CardObject dataclasses
  engine/
    decision.py             Rule-based play decisions
    actions.py              Action types
  input/
    controller.py           Keyboard and mouse execution via PyAutoGUI
tools/
  log_monitor.py            Live game-state debug monitor
  list_decks.py             Print all player decks from the log
  capture_templates.py      Helper for capturing UI button templates
  download_card_data.py     Downloads Scryfall bulk card data
tests/
  test_log_parser.py
  test_layout.py
  test_match_fsm.py
```

---

## Notes and limitations

- **Standard BO1 only.** The decision engine does not handle sideboarding or
  best-of-three game-plan adjustments.
- **Rule-based, not ML.** Card interactions not explicitly coded are passed
  through (spacebar). Complex spell chains may not be handled correctly.
- **Screen resolution.** Layout fractions in `settings.yaml` are calibrated
  for 2560×1440 and 1920×1080. Adjust `layout.*` values if cards are clicked
  in the wrong positions.
- **Use responsibly.** Automated play may violate MTG Arena's Terms of Service.
  Use at your own risk and only on accounts you own.
