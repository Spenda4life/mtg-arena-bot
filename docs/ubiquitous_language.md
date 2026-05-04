# Ubiquitous Language

This glossary defines the shared domain terms used in code, docs, tests, and AI prompts.

## Architecture Terms

- **Game state**: The mutable parser-side model built from Arena's detailed `Player.log`. It should describe game facts, not screen coordinates or input details.
- **Game snapshot**: A coordinate-free, decision-facing copy of game state. The decision engine reads snapshots and emits semantic actions.
- **Decision engine**: Arena-agnostic logic that chooses what the bot should do next from a `GameSnapshot`.
- **Action plan**: A semantic instruction from the decision engine, such as `PLAY_LAND` or `SELECT_TARGET`. It should not contain screen coordinates.
- **Execution context**: Arena UI facts captured at execution time, such as visible buttons, playable hand positions, and window bounds.
- **Execution handler**: Arena-specific orchestration that resolves an `ActionPlan` plus an `ExecutionContext` into input and verifies the expected state change.
- **Vision detector**: Image-processing code that detects UI facts from screenshots.
- **Layout mapper**: Geometry-based fallback code for estimating card and player positions.

## Game Terms

- **We / our**: The local Arena player controlled by the bot.
- **Opponent**: The remote or bot-match player across the table.
- **Hand**: Cards currently in our hand, sourced from the log.
- **Battlefield**: Permanents currently in play.
- **Stack**: Spells or abilities currently pending resolution.
- **Priority**: Whether the bot is currently allowed to take an action.
- **Phase**: Current game phase or step, normalized into names such as `MAIN1`, `COMBAT_ATTACK`, or `ENDING`.
- **Mulligan pending**: Opening-hand choice state before the first turn begins.
- **Discard required**: Cleanup state where the bot must select and confirm a discard.

## Boundary Rules

- Log-derived objects should use game terms, not UI terms.
- Decision code should talk in semantic actions, subjects, targets, and expected state changes.
- Execution code may talk in pixels, windows, buttons, screenshots, and input devices.
- Tests should name the boundary they protect when behavior crosses modules.
