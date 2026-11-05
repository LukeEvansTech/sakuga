# Design

Sakuga is laid out like a convention circle catalogue printed in reverse on oxblood: every cel is a
numbered cell in a ruled grid, and state is marked by hand in red biro. Built 2026-11-05;
`sakuga/static/app.css` is the source of truth for values.

## Palette

| Token      | Value     | Job                                                            |
| ---------- | --------- | -------------------------------------------------------------- |
| `ground`   | `#1f0a0c` | Page ground (dark mode only; the phone is read in the morning) |
| `ground-2` | `#2a0e11` | Hover and control fill                                         |
| `rule`     | `#5a1c22` | Every rule, border and grid line, 1px                          |
| `shell`    | `#7d1520` | Current tab, primary button                                    |
| `ink`      | `#efe3cf` | Text                                                           |
| `ink-2`    | `#c99a92` | Secondary text and labels                                      |
| `ink-3`    | `#9b7770` | Gone and disabled text                                         |
| `biro`     | `#f05a4f` | Hand marks only: the new-cel loop, the gone strike, failures   |
| `gold`     | `#e8b04a` | Cell codes, counts, price history                              |
| `paper`    | `#efe3cf` | Ground behind every cel photo, so paint reads true             |

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
- Biro red is a state, never decoration.
- Empty states are a ruled box with one sentence and the next step.
