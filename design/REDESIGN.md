# Lucid UI redesign — "calm gallery"

A plan to evolve Lucid's look toward the reference aesthetic (airy neutral
canvas, pure-white rounded cards with soft deep shadows, stacked "deck" framing,
quiet grey metadata over near-black body text, pill tags, circular black
actions, one color "hero" per card) — **adapted to what Lucid actually is**, not
copied. The references are an art gallery; Lucid is a notetaker. We borrow the
*devices*, not the content.

Status: plan + clickable HTML prototype (`design/screen-notes.html`) + a seed
design system pushed to Claude Design (project "Lucid"). Porting into the live
`web/styles.css` + `web/app.js` happens in phases (below).

---

## 1. Principle: translate the devices, don't paste the art

The reference cards lead with a literal abstract image. Lucid recordings have no
image. The wrong move is to bolt stock art onto note cards. The right move is to
ask *what each device is doing* and give Lucid its own version:

| Reference device | What it's doing | Lucid translation |
|---|---|---|
| Abstract gradient "art" square | Gives each card a unique visual identity + a color pop | **Generative sentiment tile** — a soft clay/slate gradient seeded by the recording (id + sentiment). No photos, always on-brand. |
| Thin black-outlined circle on the art | A focal "annotation" | Lucid's **lens motif** (Lucid = clarity/lens) — a hairline ring on the tile; optionally marks the key moment. |
| "Published on July 18, 2025" grey caption | Quiet provenance | **"Recorded · 2 days ago"** + duration, in muted grey. |
| Big near-black body paragraph | The substance | The recording's **headline + one-line summary** (already produced by analysis). |
| Light-grey pill ("smudge") | One quiet tag | Lucid's **topic / people pills** (already exist — restyle, don't reinvent). |
| Circular black "+" button | The single clear action | **Circular black actions**: a global "+ New note" FAB and a per-card "→ open". |
| Stacked cards (one peeking behind) | Depth; "there's more" | **Deck framing** for the latest note (hero) and to represent a person's many interactions. Used sparingly. |
| Lots of empty canvas | Calm, focus | **More whitespace** — bigger card padding, more gap, a cooler/quieter paper. |

## 2. Tokens (concrete changes to `:root` in styles.css)

Lucid's system already uses OKLCH + Hanken Grotesk/Instrument Serif. We nudge it
cooler and airier; we do **not** restart it.

| Token | Now | Proposed | Why |
|---|---|---|---|
| `--paper` | `oklch(0.984 0.006 83)` (warm) | `oklch(0.971 0.004 80)` | Cooler, quieter canvas like the refs' `#f4f3f2`. |
| `--card` | `oklch(1 0 0)` | unchanged | Pure white cards = the refs. |
| `--radius` | `18px` | `22px` (cards), keep `999px` pills, `14px` small | Softer, gallery-grade corners. |
| `--sh-card` (new) | — | `0 1px 2px oklch(0.4 0.02 60/.05), 0 18px 48px oklch(0.4 0.02 60/.10)` | The refs' signature: tight contact shadow + big soft drop. |
| Card padding | `15px 16px` | `18px 18px` (feed), `22px` (hero) | Air. |
| Feed `gap` | `10px` | `14px` | Air. |
| `--accent` | clay `oklch(0.60 0.135 42)` | unchanged | The clay already matches the refs' warm orange. |
| `--tile-cool` (new) | — | `oklch(0.62 0.11 245)` slate-blue | The second gradient hue (refs pair orange+blue). |

Type scale stays: Instrument Serif for big display headings (Lucid's signature),
Hanken Grotesk for everything else. Metadata `--muted` 13–14px; body `--ink`
15–16px / line-height 1.5. (No new fonts.)

## 3. Components

1. **Recording card** (`.rcard`) — keep the left-media / right-content split it
   already has. Left becomes the **generative tile** (square, 20px radius). Right:
   muted "Recorded · …" caption → serif/medium headline → one-line summary →
   footer row of topic pills + a circular "→ open". Pure white, `--sh-card`, 22px
   radius.
2. **Generative tile** (`.tile`) — layered radial + linear gradients in clay/slate,
   hue seeded per card (`--h`), hairline lens ring. Variants for sentiment
   (warm = positive, slate = neutral/analytical, muted = tense). CSS-only, no
   assets. *This is the centerpiece of the adaptation.*
3. **Hero deck** (`.deck`) — the newest note, larger, with one faint card peeking
   behind (`translate + lighter shadow`). The refs' stacked look, used once at the
   top of Notes.
4. **Pills** (`.pill`) — light-grey, fully rounded, 13px medium. Person pills get a
   tiny avatar dot. (Restyle of existing `.fchip`/topic chips.)
5. **Circular actions** — `.fab` (global, fixed bottom-right above the nav: "+ New
   note") and `.iconcircle` (per-card "→ open"). Solid `--ink`, white glyph,
   springy `:active` scale.
6. **Bottom nav** (`.tabbar`) — floating pill bar: white, rounded-999, `--sh-card`,
   inset from the edges, active tab = filled ink dot + label. (Refs' calm,
   detached feel.)

## 4. View-by-view

- **Notes (home):** hero deck (latest) → day-grouped feed of recording cards.
  Filter chips become quieter pills. FAB for new note. *(Prototyped in
  `design/screen-notes.html`.)*
- **Recording detail:** keep the audio scrubber + tabs/transcript/chat, but reframe
  the header as a wide hero card with the generative tile, headline, people pills.
  Chat input gets the pill treatment.
- **People / Directory:** person cards = avatar tile (initials over a seeded
  gradient) + name + role + a deck count of interactions. Directory "recognition"
  (new/learning/strong) becomes a small pill.
- **Ideas (ventures):** venture cards with a slate tile; the build-spec box becomes
  a clean white "spec sheet" with copy action.
- **Search:** big rounded search field (already close) on the airy canvas; results
  reuse recording cards.
- **Settings:** group rows into white rounded cards; the public-link row surfaces
  the new **permanent** link (`stable_url`) prominently.

## 5. Dark mode + accessibility

Dark theme already exists — mirror every new token (the tiles dim, not glow:
lower lightness, same hue). Keep AA contrast: body `--ink` on `--card`, captions
`--muted` ≥ 4.5:1. Circular actions keep a visible focus ring. Respect
`prefers-reduced-motion` (the deck/tap springs are decorative).

## 6. Rollout (small, safe, reviewable — and CI-gated)

1. **Tokens** — land the `:root` changes + `--sh-card`, `--tile-*`. Pure CSS, zero
   JS risk. Visible everywhere instantly.
2. **Cards** — restyle `.rcard`/`.pcard`/`.vcard` + add `.tile` (CSS only; the tile
   reads a `--h` set inline by app.js from `rec.id`).
3. **Hero deck + FAB + floating nav** — small markup tweaks in app.js.
4. **Generative tiles per sentiment** — app.js maps `analysis.sentiment` → tile
   variant; people/idea avatars.
5. **Detail / People / Ideas / Settings** — view by view.
6. **Polish** — motion, reduced-motion, dark-mode pass.

Each phase is one commit (auto-pushed) and gated by the new test CI. We iterate
the *look* in the Claude Design project ("Lucid") first, then port the agreed
components into `styles.css`/`app.js` — never a wholesale replace.

## 7. Decisions to confirm with Orion

- **Serif headlines:** keep Instrument Serif for big headings (recommended — it's
  Lucid's signature and reads more "editorial" than the refs' pure grotesk), or go
  fully grotesk like the references? *Default: keep the serif.*
- **Tile = sentiment color** (calmer, meaningful) vs **random per-id** (more
  gallery-like, less meaning)? *Default: sentiment, with id only as a seed for
  variety within a sentiment.*
- **FAB action:** "New note / upload" (default) vs record-in-browser.
