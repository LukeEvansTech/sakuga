# Design

Sakuga is laid out like a convention circle catalogue printed in reverse on graphite: every cel is a
numbered cell in a ruled grid, and state is marked by hand in amber. Built 2026-11-05;
`sakuga/static/app.css` is the source of truth for values.

## Palette

| Token      | Value     | Job                                                            |
| ---------- | --------- | -------------------------------------------------------------- |
| `ground`   | `#141414` | Page ground (dark mode only; the phone is read in the morning) |
| `ground-2` | `#1d1d1d` | Hover and control fill                                         |
| `rule`     | `#333333` | Every rule, border and grid line, 1px                          |
| `shell`    | `#2a2a2a` | Current tab (amber underline), primary button                  |
| `ink`      | `#ece8e1` | Text                                                           |
| `ink-2`    | `#b5afa5` | Secondary text and labels                                      |
| `ink-3`    | `#8a857d` | Gone and disabled text                                         |
| `biro`     | `#f5a524` | Hand marks only (amber): the new-cel loop, the gone strike     |
| `bad`      | `#e8705a` | Failures on the Log page, nothing else                         |
| `gold`     | `#c9ad7a` | Cell codes, counts, price history                              |
| `paper`    | `#ece8e1` | Ground behind every cel photo, so paint reads true             |

## Type

- Archivo Narrow (Google Fonts, 500/600/700): wordmark, cell codes, prices, counts, labels in
  uppercase with 0.1em tracking. Tabular numerals wherever numbers line up.
- System sans with Hiragino first for titles and body; Japanese titles render natively.

## Components

- **Grid**: `auto-fill` cells, 112px minimum on phones (three across at 390px), 168px from 760px.
  Rules come from the cells' right and bottom borders plus the grid's top and left: one line weight.
- **Cell**: code (`Y-0016`: site letter Y/M/S/A, catalogue number) and age; square photo on paper;
  two-line title; price; franchise.
- **Marks**: `#loop` (an open hand-drawn loop, overshooting its start) around a new cel's photo;
  `#strike` (one diagonal) across a gone cel. Nothing else is hand-drawn.
- **Tabs**: full-width ruled segments, sticky; the current page filled `shell`.
- **Facts**: ruled definition grid (uppercase narrow label over value).
- **Controls**: square selects and buttons, 40px tall, no radius.

## Rules

- No radius, no shadows, no gradients. Depth comes from rules and the paper photo ground.
- The amber biro is a state, never decoration. No red anywhere but failures (Luke disliked the oxblood scheme, 2026-11-06).
- Empty states are a ruled box with one sentence and the next step.
