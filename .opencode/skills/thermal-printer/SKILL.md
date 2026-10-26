---
name: thermal-printer
description: Use when the user asks to print a physical note, receipt, sticker, task list, QR code, or summary on the local mini thermal printer, or asks about the printer's status. Covers formatting, tool usage, and safety.
---

# Thermal printer

The project exposes a `thermal` tool namespace (or `print` / `status` tools, depending on how
OpenCode loads the plugin). The printer is an **MXW01** (the “Fun Print” cat printer): 57 mm /
384 px, BLE, 1-bit black and white. It has no color and is best for short, high-contrast text.
The same tools also work with the LuckPrinter/DP-L1S fallback family.

## When to use it

Use the print tool when the user says things like:

- "Print this"
- "Give me a receipt for the work you did"
- "Make a sticker that says ..."
- "Print a summary of the diff"
- "Print a QR code for ..."

Do **not** print automatically after every task. A physical print is a deliberate action; use it
when the user asks for one or when a printout is clearly part of the requested deliverable.

## Before printing

1. Keep it short. Aim for under ~30 printed lines; a long page can take a while over BLE and may
   run off the paper.
2. Never include secrets, API keys, tokens, passwords, private keys, or personal data unless the
   user explicitly asks and understands the risk.
3. If the user wants an image or a long document, print a short summary plus a QR code or a file
   path instead.
4. If unsure how it will look, call the print tool with `dry_run: true` first. That renders and
   saves a PNG preview without sending anything to the printer.

## Choosing a style

| Style | Use it for | Notes |
| --- | --- | --- |
| `note` | Default notes, build results, summaries | Supports headings, bullets, checkboxes, quotes, code, bold/italic |
| `receipt` | Itemized lists, costs, before/after numbers | Use two or more spaces between columns; the renderer right-aligns the second column |
| `quote` | A short inspirational or reflective line | Large serif text, centered |
| `task` | Todo lists and checklists | Use `[x]` and `[ ]`; each item gets a checkbox |
| `sticker` | Big impact text | Auto-sizes the largest font that fits; keep it to 1–5 short lines |

## Formatting examples

Build summary:

```text
title: BUILD COMPLETE
style: note
message:
**Done.** The feature is live.

- tests pass
- docs updated
- no incidents (yet)
footer: nice work
```

Task list:

```text
title: TODAY
style: task
message:
[x] review the diff
[ ] fix the flaky test
[ ] take a walk
footer: ""
```

Receipt:

```text
title: AGENT RECEIPT
style: receipt
message:
feature work        1
tests               12
bugs introduced      0
```

Sticker:

```text
style: sticker
message: SHIP
IT
footer: ""
```

## Tool inputs

The print tool accepts:

- `message` (required): markdown-lite body
- `title`: heading at the top
- `style`: `note`, `receipt`, `quote`, `task`, or `sticker`
- `footer`: small footer; `""` hides the automatic timestamp footer
- `qr`: URL or text to render as a QR code
- `image_path`: absolute path to an image to print below the text
- `copies`: 1–5
- `dry_run`: render and save a preview without printing
- `density`: 0 (light) to 2 (dark)
- `feed_dots`: extra paper feed after printing
- `label_mode`: die-cut label positioning; use only with label stock (LuckPrinter only; ignored by MXW01)

The status tool returns connection, battery, paper/cover state, model, and firmware. Check it
before printing if the user asks whether the printer is ready, or if a print fails.

## If printing fails

- Call the status tool to check for paper empty, cover open, low battery, or overheating.
- Make sure the printer is awake and the phone app (Fun Print) is not holding the BLE connection.
  Force-quit the app and turn off phone Bluetooth if necessary.
- Retry with a shorter message, lower `density`, or a larger `feed_dots`.
- If the service is not running, the plugin will try to start it. If it cannot, tell the user to
  run `uv run thermal serve --open` in the project directory and check `.thermal/daemon.log`.

Report the result plainly: what was printed, and whether it was a real print or a dry-run preview.
