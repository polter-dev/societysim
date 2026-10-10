# Avatars

Player portraits for the viewer: one per model, plus `human.webp` for visitor seats. They were generated with a Google Gemini image model, then center-cropped and resized to 512x512 webp (quality 82, no metadata). The filename is the model `name` from `configs/models.yaml` (for example `gpt-5-mini.webp`). The viewer uses them on seats, the leaderboard and model profiles (issues #302 and #297). Models without a file (such as `amd-strix-gpt-oss-20b`) fall back to the viewer's default avatar. Vite copies `public/` into `dist/`, so they are served at `/avatars/<model>.webp`.
