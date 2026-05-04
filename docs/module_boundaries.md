# Module Boundaries

The current codebase is intentionally organized around three core responsibilities:

1. Build game state from Arena logs.
2. Choose semantic actions from that state.
3. Execute those actions in the Arena client.

## Current Modules

| Area | Files | Responsibility |
| --- | --- | --- |
| Orchestration | `main.py` | Load config, wire modules, run the polling loop, handle Arena lifecycle commands. |
| Game state facade | `game_state.py` | Convert parser-side `GameState` into decision-facing `GameSnapshot`; verify expected changes. |
| Log parsing | `src/game_state/log_parser.py`, `src/game_state/state.py`, `src/game_state/grp_db.py` | Tail `Player.log`, parse GRE payloads, normalize cards, zones, turns, and players. |
| Decisions | `decision_engine.py` | Produce coordinate-free `ActionPlan` values from `GameSnapshot`. |
| Execution | `clicker_agent.py` | Capture UI context, resolve semantic targets, dispatch input, verify results, update overlay. |
| Capture and vision | `src/capture/screen.py`, `src/vision/detector.py`, `src/vision/layout.py` | Find/capture Arena, detect UI facts, estimate fallback target positions. |
| Overlay | `src/overlay.py` | Render optional debug overlay above Arena. |
| Tools | `tools/*.py` | Manual diagnostics, template capture, log/deck inspection, data download. |

## Intended Dependency Direction

```text
main.py
  -> game_state.py -> src/game_state/*
  -> decision_engine.py
  -> clicker_agent.py -> src/capture/*, src/vision/*, src/overlay.py
```

The decision engine should depend only on the public snapshot/action vocabulary.
The log parser should not depend on screen capture, layout, vision, or input.
The execution layer is the only place where pixels and input devices should meet semantic actions.

## High-Criticality Areas

- `src/game_state/log_parser.py`: incorrect parsing can corrupt every downstream decision.
- `decision_engine.py`: incorrect action selection can make the bot play poorly or loop.
- `clicker_agent.py`: incorrect target resolution or input dispatch can click the wrong UI element.

## Lower-Risk Areas

- `docs/*`
- `tools/*`
- `src/overlay.py`, when disabled by config
- Template capture/checking helpers

## Refactor Targets

- Keep `GameState` and `GameSnapshot` free of UI coordinates.
- Preserve `ExecutionHandler` as the public execution facade while extracting target resolution and input dispatch behind smaller internal interfaces.
- Move architecture tests toward boundary-level contracts: parser output, decision action plans, execution target resolution, and verification behavior.
