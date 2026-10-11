# Replay interaction specification

## Pick a game and leaderboard

Navigation, search, model/winner filters, then game links form the focus order. Enter opens a replay. Game links identify game ID, winner and duration. Announce result counts, loading and retry failures politely. Use a semantic leaderboard table with caption, scoped headers and labelled sort buttons with aria-sort. Always show sample sizes and mark missing metrics “Not measured”.

## Watch

Each seat is a labelled button identifying player, model and public life status. Use seat IDs for event anchors; labels may show names. Circle spacing supports 7–10 players. Speech bubbles repeat the public chat content and identify the speaker. Accusation arrows are solid, vote arrows dashed; text labels identify both endpoints.

Previous/Next moves one beat. Left/Right keyboard shortcuts act only outside editable fields. Home/End reaches first/latest. The timeline is a range control with a useful event label and announced value. Playback starts paused. The live badge and Back to live action explain that playback is behind the latest event. Narration is a labelled toggle with categories, and unavailable audio is explicitly disabled.

Do not continuously announce chat during autoplay. Let users pause, inspect and navigate the transcript. Decorative motion is absent under prefers-reduced-motion.

## Player inspector

Open as a labelled modal dialog; move focus to its heading, contain focus, close on Escape and restore focus to the initiating seat. Decision-history items identify day, action and actor. Activating an item seeks to its recorded beat.

Villager mode exposes only public fields. Private roles, reasoning, wolf chat and suspicion must be absent from both visual markup and the accessibility tree while a game runs. Narrator mode is an explicit viewing choice; finished-game reveal does not silently switch into it.

## Jump to lies

Label markers are named controls and never rely on color alone. Activation seeks to the source event and opens a labelled details dialog. Include recorded source, confidence and detail. Escape restores marker focus. Finished-game private reveal is opt-in. “Who fooled you” uses recorded human suspicion ratings and cannot infer a causal story.

Copying a share link announces success. Links contain only game, beat and viewing mode; seat tokens never appear in URLs.

## Visual accessibility

Control targets are at least 44×44 px. Focus uses a 3 px #ffe499 outline with a gap from the control. Body copy uses Inter; Cinzel is reserved for headings. Keep functional text on an opaque panel. Support 375 px through stacked layouts and scrollable transcripts without two-dimensional page scrolling.

Colors are documented in tokens.json. Primary/secondary copy needs WCAG AA contrast. Secondary text on day/surface is only 4.15:1: use primary text there, or move secondary copy onto day/panel. Pale gold marks on panels meet the non-text 3:1 target. The darker accusation accent is decorative and must be paired with a readable label or higher-contrast outline. The live-site accessibility check is a separate ticket (#303).
