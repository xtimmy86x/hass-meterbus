# Brand assets — attribution and licence

The artwork in this directory is **not** covered by this repository's GPL-3.0
licence. It is the property of its owner and is included under the terms below.

## M-Bus mark

| | |
|---|---|
| Owner | M-Bus Usergroup (`m-bus.com`) |
| Source page | <https://m-bus.com/documentation-wired/04-physical-layer> |
| Source | inline `<svg>` in the site header, linked to `/` |
| Retrieved | 2026-08-09 |
| Original file | `mbus-logo-original.svg` (kept here unmodified) |

The mark is the "M-Bus" wordmark: a zigzag **M** drawn as a signal trace, the
hyphen and "Bus" set beside it, and a rule running along the baseline.

`icon.svg`, `icon.png` (256×256) and `icon@2x.png` (512×512) are derived from
that file:

- the mark was **scaled and centred** on a white square, at 92% of its width,
  and exported at 256×256 and 512×512 to meet the
  [Home Assistant brands](https://github.com/home-assistant/brands) icon spec;
- an explicit black fill was set, because the original takes its colour from
  the website's stylesheet and carries none of its own.

Nothing was cropped, recoloured, redrawn or rearranged. Every glyph and the
baseline rule are present, in their original proportions and arrangement.

## Why it is here

Home Assistant identifies each integration by the logo of the device, protocol
or service it talks to — that is the entire purpose of the `brands` repository,
and since Home Assistant 2026.3 a custom integration may ship the same assets
locally. The mark is used **descriptively**, to identify the M-Bus protocol
this integration speaks.

"M-Bus" and the M-Bus mark are the property of the M-Bus Usergroup. This
project is **not affiliated with, endorsed, sponsored by, or supported by**
the M-Bus Usergroup, nor by any meter manufacturer. Use of the mark here does
not imply any such relationship.

If the M-Bus Usergroup would prefer the mark not be distributed in this
repository, open an issue and it will be removed promptly.
