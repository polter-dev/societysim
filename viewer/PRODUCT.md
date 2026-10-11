# Product

<!-- impeccable:product-schema 1 -->

## Platform

web (desktop browsers only)

## Platform scope

Desktop only. Mobile and responsive layouts are out of scope.

## Users

People watching AI agents play Werewolf, including people who have never played the game. The confirmed design brief is issue #22; researchers compare recorded model behavior through the index and leaderboard.

## Product Purpose

Replay logged Werewolf games, inspect recorded decisions, and understand deception through explicit evidence.

## Operating Context

The React/Vite viewer reads a published index and JSONL recordings. Local JSONL upload is a fallback. Visitors can also play: the landing page offers Normal, Watch and Advanced modes, backed by a Play API. A global leaderboard (Detectors and Liars) is live, built from recorded games.

## Capabilities and Constraints

A deterministic Python engine owns game state. The viewer derives scenes from logged events. Actor and target identifiers are seat IDs. Public and private views are distinct; hidden information must never leak into a running villager view. Raw logs are immutable; labels are sidecars. Missing content is not invented.

## Brand Commitments

Society Sim. Preserve the user's illustrated forest, gold frames, flowers, lanterns and table artwork from the linked Werewolf — Game Viewer Figma file. Use plain, human-facing text.

## Evidence on Hand

Actual contracts and fixtures in schema/, fixtures/ and docs/DATA_CONTRACT.md at the repository root. Design references and exports are in ../docs/design/. Wireframe sample games and statistics are illustrative, not findings.

## Product Principles

- Let a newcomer follow a replay before needing the rules.
- Make every claim traceable to a recorded event or label.
- Keep viewing mode and private-information boundaries explicit.
- Keep keyboard and screen-reader users able to pause, navigate and inspect.

## Accessibility & Inclusion

Keyboard-only operation, named controls, modal focus restoration, readable day/night contrast and reduced motion are part of the confirmed viewer brief.
