# Token contrast check

WCAG relative-luminance ratios, calculated from the exported opaque sRGB colors. Body copy requires at least 4.5:1; large text 3:1. These checks cover tokens, not the live artwork or application.

| Background | Primary text | Secondary text |
| --- | ---: | ---: |
| day/canvas | 9.85:1 | 8.97:1 |
| day/panel | 11.81:1 | 10.75:1 |
| day/surface | 4.55:1 | **4.15:1 — do not use for body copy** |
| night/canvas | 15.71:1 | 14.30:1 |
| night/panel | 13.34:1 | 12.14:1 |
| night/surface | 10.23:1 | 9.31:1 |

Preserve the existing Figma palette. On day/surface, use primary text; secondary copy belongs on day/panel. Functional labels need opaque backgrounds and visible focus. Colored event arrows also need readable labels and distinguishable patterns.
