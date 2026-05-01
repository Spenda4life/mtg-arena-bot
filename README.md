# MTG Arena Bot

An experimental MTG Arena bot for Standard BO1 and bot-match validation. The
repo is organized around a strict split between:

- log-derived game state
- Arena-agnostic decisions
- Arena-specific execution

That split is the main design constraint. The decision engine should produce
semantic actions, not coordinates. The clicker layer is responsible for turning
those semantic actions into keyboard and mouse input.

## Current Status

The bot can:

- detect whether Arena is running
- read Arena's detailed `Player.log`
- build a coordinate-free game snapshot
- choose simple rule-based actions
- detect keep, mulligan, discard, and some button states visually
- execute keypresses and clicks through `pyautogui`
- verify expected state changes from the log
- stop after repeated identical execution failures
- optionally draw a lightweight debug overlay showing intended targets

Recent live validation confirmed that home-screen idling and mulligan handling
work. First-main-phase land play is still under active debugging: when playable
hand-card vision fails, the fallback hand geometry can click the wrong card in
Arena's fanned hand layout.

## Runtime Flow

```text
Arena Player.log
  -> ArenaLogParser
  -> GameStateManager
  -> GameSnapshot
  -> DecisionEngine
  -> ActionPlan
  -> ExecutionHandler
  -> ScreenCapture + VisionDetector + CardPositionMapper
  -> pyautogui input
  -> verification against refreshed GameSnapshot
```

Arena's log is treated as the source of truth for game state. Screen capture is
used only for UI facts that the log does not provide, such as button positions,
discard prompts, playable hand outlines, and click targets.

## Key Files

```text
main.py                     CLI entry point and bot loop
game_state.py               Public game-state manager and snapshots
decision_engine.py          Rule-based semantic decision engine
clicker_agent.py            Execution handler and click verification

src/arena_process.py        Arena launch, kill, and status helpers
src/overlay.py              Optional lightweight debug overlay
src/capture/screen.py       Arena window discovery and MSS capture
src/vision/detector.py      Template matching and visual hints
src/vision/layout.py        Fallback coordinate mapper
src/game_state/log_parser.py Player.log JSON stream parser
src/game_state/grp_db.py    Arena card database lookup
src/game_state/state.py     Parser-side state dataclasses

tools/log_monitor.py        Live log/state monitor
tools/list_decks.py         Deck inspection helper
tools/capture_templates.py  UI template capture helper
tools/download_card_data.py Card data helper

tests/test_log_parser.py
tests/test_layout.py
tests/test_three_module_architecture.py
```

The current source path does not include an automatic game-start navigator. The
active `run` command waits for a game to start and then acts on the game state.

## Prerequisites

- Python 3.12+
- MTG Arena installed locally
- Arena detailed logging enabled
- Tesseract OCR installed and available to `pytesseract`
- Python dependencies from `requirements.txt`

Install dependencies:

```bash
pip install -r requirements.txt
```

Enable detailed logging in Arena:

1. Open Arena.
2. Go to Settings -> Account.
3. Enable Detailed Logs.
4. Restart Arena.

## Commands

Run the bot loop against an already-open Arena client:

```bash
python main.py
python main.py run
python main.py run --no-launch
```

Launch Arena before running:

```bash
python main.py run --launch
```

Process helpers:

```bash
python main.py status
python main.py launch
python main.py kill
```

Run tests:

```bash
pytest
```

## Configuration

Defaults live in `config/settings.yaml`. Local overrides live in
`config/settings.local.yaml`, which is gitignored.

Useful options:

```yaml
arena:
  manage_lifecycle: false
  poll_interval: 0.5
  action_delay: 0.8
  pre_click_delay: 0.0
  verification_timeout: 5.0

vision:
  template_threshold: 0.88
  debug_overlay: false
  debug_screenshots: false

engine:
  aggression: 0.7
  max_consecutive_failures: 3

layout:
  hand_y: 0.905
  hand_x_min: 0.175
  hand_x_max: 0.825
```

Set `vision.debug_overlay: true` locally when diagnosing click targets. The
overlay is intentionally lightweight: it redraws only when new action data
arrives, refreshes at a low rate, and hides quickly when stale. It is still a
transparent topmost window over a game, so leave it off for normal play.

## Live Testing

Follow `TESTING_CHECKLIST.md` for full live-client validation. A practical
manual loop is:

1. Start Arena and sit on the home screen.
2. Run `python main.py run --no-launch`.
3. Start a bot match manually.
4. Watch `logs/bot.log`.
5. Confirm decisions are followed by verified state changes.
6. Stop and inspect failures before letting repeated actions continue.

Important log lines:

```text
Starting MTG Arena bot loop
Arena is open; waiting for a game to start
Mulligan pending changed: ...
Phase changed: ...
Executing action: ...
Verified action success: ...
Stopping after ... consecutive failures ...
```

## Known Limitations

- The bot is rule-based and intentionally simple.
- Standard BO1 is the target; sideboarding is not handled.
- Card-specific tactics are limited.
- Screen-space execution is still fragile.
- Hand-card targeting needs more work for Arena's fanned layout.
- The current source does not automatically navigate from home screen into a
  match.
- Use responsibly. Automated play may violate MTG Arena's Terms of Service.
