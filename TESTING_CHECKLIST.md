# Testing Checklist

Use this checklist for the first live Arena validation after the architecture cleanup.

## Before You Start

1. Enable Arena detailed logs.
2. Set `logging.level: "DEBUG"` in `config/settings.local.yaml`.
3. Keep `action_delay: 0.8` or higher for the first run.
4. Start the log monitor in a second terminal:

```bash
python tools/log_monitor.py
```

5. Confirm the bot writes to `logs/bot.log`.

## Required Debug Logs

During testing, confirm these log lines appear in `logs/bot.log`:

- `Phase changed: ...`
- `Mulligan pending changed: ...`
- `Discard required changed: ...`
- `Decision: ...`
- `Execution context: keep=... mulligan=... discard=... playable=...`
- `Resolved hand card ...`
- `Resolved battlefield card ...`
- `Resolved player target: opponent -> ...`
- `Verified action success: ...`

If any of those are missing during the matching scenario, treat that path as not yet validated.

## Test Order

1. Launch Arena manually and sit at the home screen.
2. Run:

```bash
python main.py run
```

3. Verify the bot stays idle without crashing when no game is active.
4. Start a game and validate mulligan handling.
5. Validate first main phase land play.
6. Validate first spell cast from hand.
7. Validate targeted spell resolution against opponent face.
8. Validate targeted spell resolution against an opposing creature.
9. Validate combat attacker selection.
10. Validate combat blocker selection.
11. Validate discard-to-hand-size handling if the scenario appears.
12. Let the bot continue for several turns and watch for repeated failed verification loops.

## Pass Criteria

- No exceptions in the terminal.
- `logs/bot.log` shows semantic decisions followed by successful execution verification.
- Hand size decreases after land plays and spell casts when expected.
- Targeted spells resolve to the intended Arena target.
- No repeated unresolved actions for the same card across many ticks.

## Failure Triage

- If mulligans fail: inspect `mulligan_pending` transitions in the log.
- If hand clicks fail: inspect `Resolved hand card ...` lines and compare against live UI.
- If targeting fails: inspect `Resolved player target ...` or `Resolved battlefield card ...` lines.
- If verification fails after a visually correct action: inspect `expected_state_change` assumptions in `decision_engine.py`.
- If the bot clicks the wrong battlefield permanent: tune `layout.*` values in `config/settings.local.yaml`.
