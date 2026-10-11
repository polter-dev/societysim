---
name: Society Sim viewer
description: Illustrated forest replay viewer
colors:
  day-canvas: "#243d2b"
  day-panel: "#183020"
  day-surface: "#3f7546"
  night-canvas: "#03131d"
  night-panel: "#03271e"
  night-surface: "#123c2e"
  text-primary: "#f4ebc2"
  text-secondary: "#d8e4d1"
  accent-gold: "#d4b35c"
  focus-ring: "#ffe499"
typography:
  heading:
    fontFamily: "Cinzel, Georgia, serif"
    fontSize: "28px"
    fontWeight: 600
    lineHeight: "36px"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: "21px"
rounded:
  control: "8px"
  panel: "16px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  section: "48px"
components:
  control:
    backgroundColor: "{colors.night-surface}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.control}"
    height: "44px"
---

## Overview

The user's Figma file is the design authority. See ../docs/design/README.md for exact frame links and the exported foundations. Original illustrated art and the editable behavior wireframes are complementary references. This record initializes Impeccable context; it does not claim the application has already adopted every token.

## Colors

Use day/night palettes deliberately, with primary and secondary copy on opaque surfaces. Day/surface requires primary text: secondary text there is only 4.15:1. Gold indicates controls; pale gold indicates focus. Accusation and vote semantics require labels and different patterns, not color alone. Full event accents and typography are in ../docs/design/tokens.json.

## Typography

Use the existing Figma Cinzel heading styles and Inter body/label styles. Header 44 px, page 28 px, section 18 px; body 16/14/12 px. Do not bake functional labels into PNGs. Font loading is a separate implementation concern; Georgia/system-ui are fallbacks.

## Layout

Use the 4–48 px spacing scale. The table supports 7–10 seats, with speech and arrow endpoints tied to seat positions. At 375 px, stack ancillary panels and maintain readable transcripts. Center buttons against their visible artwork, accounting for transparent PNG margins.

## Elevation & Depth

Forest artwork supplies decorative depth. Opaque emerald panels separate functional copy from busy backgrounds. Focus must stay visible over frames and illustrations.

## Shapes

Control radius 8 px; panel radius 16 px. Interactive targets are at least 44 px in both dimensions. Gold illustration borders do not shrink the hit area.

## Components

Seat buttons open an inspector. Modal close actions restore focus. Lie markers seek to recorded events. Timeline and step controls expose current position. Narration, playback and viewing-mode changes are labelled. Detailed behavior is in ../docs/design/interaction-spec.md.

## Do's and Don'ts

Keep private information out of running public views. Show recorded text verbatim. Label unavailable metrics and audio honestly. Respect reduced motion. Never fabricate game history or use artwork contrast as proof of accessible text.
