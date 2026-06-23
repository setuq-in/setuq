# setuq brand kit

Closed-loop cycle (execute → analyze → decide → audit) cradling a sovereign shield.
Built on *setu* = "bridge" → **Splunk today, every SIEM tomorrow.**

## Palette

| Token | Hex | Use |
|-------|-----|-----|
| Loop purple | `#7e14ff` | gradient start, primary |
| Bridge violet | `#863bff` | mid / solid fallback |
| Cyan accent | `#47bfff` | gradient end, arrowhead, highlights |
| Ink | `#0b0c0e` | dark backgrounds |
| Mist | `#f3f4f6` | text on dark |

Gradient: `linear-gradient(135deg, #7e14ff → #47bfff)`.

## Assets

| File | What | Where |
|------|------|-------|
| `setuq-mark.svg` | full mark (loop + shield + check), gradient | app, square avatars |
| `setuq-favicon.svg` | simplified mark (loop + core), crisp ≤16px | browser tab |
| `setuq-mark-mono.svg` | one-color line art (`currentColor`) | stamps, light/dark inline |
| `setuq-wordmark.svg` | mark + "setuq" lockup | nav bar, docs header |
| `setuq-banner.svg` | dark hero w/ tagline | README top, GitHub profile |
| `preview.html` | contact sheet of all variants | open in browser |

## Quick use

**Markdown / README**
```md
<p align="center"><img src="brand/setuq-banner.svg" width="100%" alt="setuq"></p>
```

**GitHub profile** — upload `setuq-mark.svg` (export 400×400 PNG) as org/avatar; use `setuq-banner.svg` as the profile-README hero.

**App** — `favicon.svg` is already wired in `ui/index.html`; `setuq-mark.svg` + `setuq-wordmark.svg` live in `ui/public/`.

> GitHub READMEs don't render `<text>` fonts identically everywhere — for the avatar, export the SVG to PNG first (`setuq-mark.svg` at 512px).
