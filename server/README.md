---
title: SocietySim Play
emoji: 🐺
colorFrom: gray
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# SocietySim Play server

This Space runs the game server behind visitor play on https://society-sim.us. A visitor
sits in one seat of a Werewolf game; the other seats are LLM agents. The engine, rules and
API are in the main repo: https://github.com/polter-dev/societysim.

The Space is a deploy target. Don't edit it by hand: `scripts/deploy-play-space.sh` in the
repo rebuilds it. Secrets (provider keys, Neon, Turnstile) are Space secrets, set by name by
that script. The server refuses to start without Turnstile and Neon configured.

Health check: `GET /api/health`. Kill switch: set the Space variable `PLAY_ENABLED=0`.
