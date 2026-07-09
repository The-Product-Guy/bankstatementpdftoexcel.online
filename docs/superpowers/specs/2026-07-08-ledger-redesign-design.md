# Ledger Redesign — Full-Site UI System

**Date:** 2026-07-08
**Status:** Approved
**Owner:** Sasi (approved via Claude Code session)
**Source design:** `website-redesign-request/project/Statement Converter.dc.html` + `Pricing.dc.html` (Claude Design handoff bundle)

## Problem

The current UI reads as generic AI-generated SaaS ("AI slop"): Inter font,
default light theme, fake static hero preview, no distinctive point of view.
The user supplied a Claude Design mockup with a strong editorial identity.
Decision: adopt that design system faithfully rather than invent a competing
one, with two corrections — keep the **Statement Converter** product name
(the mockup renames to "Ledger&Line"), and make the pricing page
**truth-first** (the mockup invents features, limits, and prices).

## Decisions (locked with owner)

1. **Brand:** Product name stays "Statement Converter" (SEO domain + Ambion
   equity). Full visual adoption of the mockup: ink/cream/coral palette,
   Newsreader/Manrope/JetBrains Mono. Navy `#000a63` / blue `#046bca`
   retired. New logo mark: ink rounded square with coral + cream pixels;
   wordmark in Newsreader. Ambion attribution stays in the footer.
2. **Pricing truth:** Real plans only — Free $0 (5/mo, 20 MB), Pro $9.99
   (50/mo, 100 MB, featured dark card), Enterprise $29.99 (unlimited,
   500 MB). No yearly toggle (no yearly Stripe prices exist). No invented
   features (no team seats, batch, priority queue, retention tiers).
3. **Scope:** All 12 templates + `styles.css` + logo asset. One design
   system everywhere.

## Design system (tokens)

| Token | Value | Use |
|---|---|---|
| `--cream` | `#faf8f4` | Page background, light cards, text-on-dark |
| `--ink` | `#12182a` | Text, dark cards/bands, buttons |
| `--ink-deep` | `#0e1420` | Hero/CTA band background |
| `--coral` | `#ff6b4a` | Accent: CTAs, eyebrows, hover, selection |
| `--lime` | `#b4ff78` | Accent on dark sections only |
| `--mist` | `#8fd3ff`, `--gold` `#ffd76b` | Feature-chip colors |
| `--ok-green` | `#1d7a3f` | Success/xlsx indicator |
| Hairline | `rgba(18,24,42,0.08–0.10)` | Borders, 1px-gap grids |
| Muted text | `#12182a99` light / `rgba(250,248,244,0.6–0.75)` dark | Secondary copy |

- **Type:** Newsreader (serif; display headlines 500–600, stat numerals,
  logo wordmark), Manrope (400–800; body, UI, buttons), JetBrains Mono
  (filenames, data values, spec-sheet values). Display sizes via `clamp()`.
  Inter removed everywhere.
- **Shape:** pill buttons (`border-radius:100px`), cards 16–22px radius,
  final-CTA band 28px radius inset from viewport edge.
- **Signature patterns:** sticky blurred nav (`backdrop-filter`) with pill
  CTA; dark hero with radial coral glow + drifting blob; rotated
  PDF→XLSX transformation cards (pure-CSS `rowFill`/`gridGlow` animations);
  capability chip strip; numbered workflow cards in a 1px-gap grid;
  dark "accuracy" band with 4 feature cards; **spec-sheet rows**
  (label left, mono value right, hairline-separated) for security rules,
  job status, and account data; editorial footer.
- **Motion:** `floatUp`, `rowFill`, `gridGlow`, `drift` keyframes from the
  mockup; all gated behind `@media (prefers-reduced-motion: no-preference)`.

## Per-page mapping

| Template | Treatment |
|---|---|
| `base.html` | New nav + footer, Google Fonts swap (Newsreader/Manrope/JetBrains Mono), tokenized styles link. Keep all meta/canonical/JSON-LD blocks and socket.io loading as-is. |
| `home.html` | Mockup 1:1 with truth edits. Trust stats: "1 free conversion — no sign-up", "5 / month with a free account", "Files auto-expire". Capability chips: Text PDFs, Scanned PDFs, Multi-page tables, 500 MB uploads (Enterprise). Nav: How it works, Pricing, Blog, Security. CTAs → `/dashboard` (converter) and `#workflow`. Hero visual stays abstract (no fake data). Workflow/accuracy/security/final-CTA sections as designed; copy from mockup (already truthful). |
| `pricing.html` | Mockup card layout; real plans per Decisions §2. Pro = featured dark card ("Most popular"). Feature lists from the current honest page (post-PR#2) restyled. FAQ accordion (design's + / rotate-45 affordance) keeping existing FAQPage JSON-LD; answers updated to match truth. |
| `dashboard.html` | System extension: cream app shell; upload dropzone as the hero object (dashed ink border, coral hover/drag state); progress as spec-sheet rows with mono values; result card styled like a workbook (ink header row, hairline grid, right-aligned mono numerals). **Every id/class/data-attr referenced by `static/script.js` is inventoried first and preserved — no JS changes.** |
| `signin.html` | Centered card on cream; Newsreader headline; magic-link form; pill submit (ink → coral hover); honest quota note (1 guest / 5 free-account). |
| `account.html` | Spec-sheet rows for plan, usage, limits; mono values; Stripe portal + signout as pill buttons. |
| `blogs.html`, `blog_post.html` | Editorial: serif display, hairline-divided article list; post body ~70ch measure, Newsreader headings, Manrope body. |
| `privacy.html`, `terms.html` | Prose pages in editorial treatment; same nav/footer. |
| `processing.html` | Job-status page as spec-sheet + progress states. |
| `admin.html` | Minimal reskin (internal tool): tokens + table styling only. |

Logo: replace `static/ambion-logo.svg` usage with new mark
(`static/logo-mark.svg` + wordmark text); keep an Ambion credit line in the
footer ("A product of Ambion Softwares").

## Engineering rules

- Rewrite `static/styles.css` from scratch as CSS custom properties +
  semantic classes. **Do not copy the prototype's inline styles**; the
  mockup is a visual spec, not code. `sc-for`/`sc-if` → Jinja loops/ifs.
- Semantic HTML: `<nav>`, `<button>`, `<a>`, heading hierarchy; the
  mockup's div-soup is not carried over.
- Accessibility: text contrast ≥ 4.5:1 (muted-on-cream and muted-on-ink
  verified); `:focus-visible` rings (coral); reduced-motion support; FAQ
  accordion keyboard-operable (`<button>` + `aria-expanded`).
- Preserve: all Flask endpoints, form actions/methods, CSRF/flash handling,
  meta/OG/canonical/JSON-LD, sitemap-referenced URLs, h1-per-page.
- `tests/test_routes.py` asserts headline strings (e.g.
  `<h1>Simple, Transparent Pricing</h1>`, "Blog and Guides"); update those
  assertions to the new copy **in the same commit** as the template change.
- No new JS dependencies. FAQ accordion = ~20 lines vanilla or
  `<details>`-based if styling allows.

## Out of scope (follow-ups)

- SEO content round (per-bank landing pages, blog restructure) — task #6.
- Downloadable real sample workbook + real-output preview module.
- Yearly billing (needs Stripe yearly prices first).
- socket.io off marketing pages / og-image weight (SEO round).

## Test & verification plan

- `python -m pytest tests/ -v` green after each page's commit.
- Visual pass with browser tools on: home, pricing, signin, dashboard
  (upload → progress → result with a small text PDF under
  `local-low-mem`), blogs, account. Check sticky nav, accordion, hover
  states, reduced-motion, 375px mobile layout.
- Grep-check: no `Inter`, `#000a63`, `#046bca`, `Font Awesome` references
  remain; no `Ledger&Line` string ships.
