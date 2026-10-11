# Viewer design / T4.0

The source is [Werewolf — Game Viewer](https://www.figma.com/design/eWOVhSAOmFM0jycNu3TTPd/Werewolf---Game-Viewer). The original Landing Page and Main website pages remain intact. Editable wireframes and annotations live on [T4.0 — Viewer flows & tokens](https://www.figma.com/design/eWOVhSAOmFM0jycNu3TTPd/Werewolf---Game-Viewer?node-id=54-161).

## Frames

| Flow | Figma frame |
| --- | --- |
| 01 / Pick a game | [Open frame](https://www.figma.com/design/eWOVhSAOmFM0jycNu3TTPd/Werewolf---Game-Viewer?node-id=54-164) |
| 02 / Watch the village | [Open frame](https://www.figma.com/design/eWOVhSAOmFM0jycNu3TTPd/Werewolf---Game-Viewer?node-id=54-174) |
| 03 / Inspect a player | [Open frame](https://www.figma.com/design/eWOVhSAOmFM0jycNu3TTPd/Werewolf---Game-Viewer?node-id=54-213) |
| 04 / Jump to lies | [Open frame](https://www.figma.com/design/eWOVhSAOmFM0jycNu3TTPd/Werewolf---Game-Viewer?node-id=54-222) |
| 05 / Leaderboard | [Open frame](https://www.figma.com/design/eWOVhSAOmFM0jycNu3TTPd/Werewolf---Game-Viewer?node-id=54-231) |
| 06 / Design foundations | [Open frame](https://www.figma.com/design/eWOVhSAOmFM0jycNu3TTPd/Werewolf---Game-Viewer?node-id=54-238) |
| Original landing / mode / setup | [Landing Page](https://www.figma.com/design/eWOVhSAOmFM0jycNu3TTPd/Werewolf---Game-Viewer?node-id=45-535) |
| Original illustrated table / inspector | [Main website](https://www.figma.com/design/eWOVhSAOmFM0jycNu3TTPd/Werewolf---Game-Viewer?node-id=1-2) |

The prototype uses in-page scroll links for Watch, Leaderboard, player seats, Inspect, Jump to lies, and return actions. The wireframes use editable text, frames, vectors, and action instances; they are not screenshots of the app. Example names, games and statistics are sample content.

## Exports

- `tokens.json`: Figma variable values and existing text-style definitions.
- `tokens.css`: matching CSS custom properties, ready for a separate implementation ticket.
- `interaction-spec.md`: keyboard, screen-reader and visibility rules.
- `../../viewer/PRODUCT.md` and `../../viewer/DESIGN.md`: Impeccable product/design context.

Day colors and Cinzel/Inter styles come from the existing Figma design. Night colors, gold/focus accents, spacing and radii document the illustrated viewer conventions. The new Viewer foundations collection uses explicit day/night variable names in a single mode, so it works without a paid multi-mode Figma plan. Existing variables were preserved.

This ticket records design intent. It does not implement missing viewer controls, load fonts, or replace application CSS. Those changes belong to the viewer implementation tickets.

## Validation

The wireframes were visually checked after fixing multiline text sizing. Read-back confirmed Cinzel/Inter text, 18 prototype actions, and no image-filled nodes. JSON/CSS values were compared with the Figma export. Contrast checks cover primary/secondary text on both palettes and gold/focus accents on opaque panels.

Functional text must use an opaque surface; artwork pixels are not a predictable contrast background. Event accents never carry meaning alone. Keyboard and screen-reader annotations are specifications, not a claim that the live site has passed its accessibility audit.

## Impeccable

Current Impeccable uses `PRODUCT.md` for product context and `DESIGN.md` for design direction; these viewer-local records initialize that context from this ticket. See the [official initialization reference](https://github.com/pbakaus/impeccable/blob/main/skill/reference/init.md). The interactive `/impeccable init` command was not run in this session. No runtime dependencies or global skills were installed.
