# Ledger Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild all 12 templates + `static/styles.css` on the "Ledger" design system (ink/cream/coral, Newsreader/Manrope/JetBrains Mono) from the approved spec, keeping the Statement Converter brand and truth-first copy.

**Architecture:** One tokenized stylesheet (`static/styles.css`, rewritten from scratch) + semantic classes consumed by all Jinja templates extending `base.html`. The Claude Design mockups in `website-redesign-request/project/` are the visual source of truth; templates are hand-written semantic HTML — never copies of the prototype's inline styles. Flask routes, forms, JS, and SEO metadata are untouched.

**Tech Stack:** Jinja2 templates, vanilla CSS (custom properties), Google Fonts (Newsreader, Manrope, JetBrains Mono), existing `static/script.js` (unchanged).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-08-ledger-redesign-design.md` (read first).
- Visual source: `website-redesign-request/project/Statement Converter.dc.html` (home) and `Pricing.dc.html` (pricing). Read them before styling; match their look, not their markup.
- Product name everywhere: **Statement Converter**. The string `Ledger&Line` must never ship. Footer keeps "A product of Ambion Softwares".
- Palette only: cream `#faf8f4`, ink `#12182a`, ink-deep `#0e1420`, coral `#ff6b4a`, lime `#b4ff78` (dark sections only), mist `#8fd3ff`, gold `#ffd76b`, ok-green `#1d7a3f`. Navy `#000a63` / blue `#046bca` / Inter are retired — zero references at the end.
- Truthful copy only: Free $0 (5/mo, 20 MB) · Pro $9.99/mo (50/mo, 100 MB) · Enterprise $29.99/mo (unlimited, 500 MB). Guest = 1 free conversion. No batch, no team seats, no priority queue, no retention tiers, no yearly pricing, no API.
- Every page keeps: its Flask endpoints/form actions, canonical/OG/JSON-LD blocks, exactly one `<h1>`.
- All animations inside `@media (prefers-reduced-motion: no-preference)`.
- After every task: `python -m pytest tests/ -q` green (update string assertions in the same commit).
- Commit format: imperative subject + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer.

---

### Task 1: Design foundation — tokens, base.html, nav/footer, logo

**Files:**
- Modify: `static/styles.css` (full rewrite)
- Modify: `templates/base.html` (font links, nav, footer)
- Create: `static/logo-mark.svg`
- Test: existing `tests/test_routes.py` (no assertion changes expected)

**Interfaces:**
- Produces: CSS custom properties and classes all later tasks use: `.nav`, `.nav-links`, `.btn-pill`, `.btn-pill--coral`, `.btn-pill--ghost-dark`, `.eyebrow`, `.display`, `.card`, `.hairline-grid`, `.spec-rows`, `.spec-row`, `.chip`, `.band-dark`, `.cta-band`, `.footer`, `.mono`, `.muted`.
- Produces: Jinja blocks in `base.html` unchanged in name (`title`, `meta_description`, `content`, etc.) — later tasks rely on them.

- [ ] **Step 1: Create `static/logo-mark.svg`** — ink rounded square, coral + cream pixel squares (mirrors mockup lines 29–32):

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 30" width="30" height="30" role="img" aria-label="Statement Converter">
  <rect width="30" height="30" rx="8" fill="#12182a"/>
  <rect x="6" y="6" width="8" height="8" rx="2" fill="#ff6b4a"/>
  <rect x="16" y="16" width="8" height="8" rx="2" fill="#faf8f4"/>
</svg>
```

- [ ] **Step 2: Rewrite `static/styles.css` head section** — delete the old file content and write the token base + primitives. Core block (complete tokens; component classes follow the same naming):

```css
:root {
  --cream: #faf8f4;
  --ink: #12182a;
  --ink-deep: #0e1420;
  --coral: #ff6b4a;
  --lime: #b4ff78;
  --mist: #8fd3ff;
  --gold: #ffd76b;
  --ok-green: #1d7a3f;
  --muted: rgba(18, 24, 42, 0.60);
  --muted-strong: rgba(18, 24, 42, 0.75);
  --hairline: rgba(18, 24, 42, 0.09);
  --on-dark: #faf8f4;
  --on-dark-muted: rgba(250, 248, 244, 0.65);
  --on-dark-hairline: rgba(250, 248, 244, 0.12);
  --font-display: 'Newsreader', Georgia, serif;
  --font-body: 'Manrope', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, monospace;
  --radius-card: 18px;
  --radius-big: 28px;
  --radius-pill: 100px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: var(--font-body);
  background: var(--cream);
  color: var(--ink);
  overflow-x: hidden;
}
::selection { background: var(--coral); color: var(--ink-deep); }
:focus-visible { outline: 2px solid var(--coral); outline-offset: 2px; }
.display { font-family: var(--font-display); font-weight: 500; letter-spacing: -0.02em; line-height: 1.06; }
.eyebrow { font-size: 13px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--coral); }
.mono { font-family: var(--font-mono); }
.muted { color: var(--muted); }
.btn-pill { display: inline-flex; align-items: center; justify-content: center; gap: 10px; padding: 13px 26px; border-radius: var(--radius-pill); background: var(--ink); color: var(--cream); text-decoration: none; font-weight: 700; font-size: 14.5px; border: none; cursor: pointer; transition: transform .15s, background .15s, color .15s; }
.btn-pill:hover { background: var(--coral); color: var(--ink-deep); transform: translateY(-1px); }
.btn-pill--coral { background: var(--coral); color: var(--ink-deep); }
.btn-pill--coral:hover { background: var(--cream); color: var(--ink); }
.btn-pill--ghost-dark { background: transparent; border: 1px solid rgba(250,248,244,.25); color: var(--cream); }
.spec-rows { display: flex; flex-direction: column; gap: 1px; background: var(--hairline); border-radius: var(--radius-card); overflow: hidden; }
.spec-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 24px 28px; background: var(--cream); }
.spec-row > .value { font-family: var(--font-mono); font-size: 13.5px; color: var(--muted); }
.hairline-grid { display: grid; gap: 1px; background: var(--hairline); border-radius: 20px; overflow: hidden; }
.hairline-grid > * { background: var(--cream); padding: 44px 36px; }
.band-dark { background: var(--ink); color: var(--on-dark); }
@media (prefers-reduced-motion: no-preference) {
  @keyframes floatUp { from { opacity: 0; transform: translateY(18px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes rowFill { 0% { transform: scaleX(0); opacity: 0; } 60% { opacity: 1; } 100% { transform: scaleX(1); opacity: 1; } }
  @keyframes gridGlow { 0%, 100% { opacity: .5; } 50% { opacity: 1; } }
  @keyframes drift { from { transform: translate(0,0); } to { transform: translate(-60px,-40px); } }
  .anim-float { animation: floatUp .7s ease both; }
}
```

Then add, in the same file: `.nav` (sticky, `background:rgba(250,248,244,.86)`, `backdrop-filter:blur(10px)`, hairline bottom border, flex, `padding:20px clamp(20px,5vw,56px)`), `.nav-links a` (ink, 600, 14.5px, coral on hover), `.footer` (grid `1.4fr 1fr 1fr 1fr`, hairline top rule, muted small text), `.card` (white/cream surface, hairline border, `border-radius:var(--radius-card)`), `.chip` (coral dot + 13.5px 600 muted), `.cta-band` (ink-deep, `border-radius:var(--radius-big)`, `margin:0 40px 40px`, centered, radial coral glow via `::before`), form controls (`input, select, textarea` — cream surface, hairline border, 10px radius, Manrope, coral focus ring), `.alert` styles (keep class name; success = ok-green tint, error = coral tint), and a `main.page` wrapper (`max-width:1240px; margin:0 auto; padding:0 clamp(20px,5vw,56px)`). Mobile: single `@media (max-width: 760px)` collapsing grids to one column and nav links to wrap.

- [ ] **Step 3: Update `templates/base.html`** — replace the Inter font link (line 21) with:

```html
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400&family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
```

Replace the existing header/nav markup with (keep every `url_for` endpoint already present in the old nav — inspect before deleting; guests see Sign in, authed users see Dashboard/Account/Sign out exactly as the old nav did):

```html
<nav class="nav">
  <a class="nav-brand" href="{{ url_for('pages.home') }}">
    <img src="{{ url_for('static', filename='logo-mark.svg') }}" alt="" width="30" height="30">
    <span class="brand-word">Statement Converter</span>
  </a>
  <div class="nav-links">
    <a href="{{ url_for('pages.home') }}#workflow">How it works</a>
    <a href="{{ url_for('pages.pricing') }}">Pricing</a>
    <a href="{{ url_for('pages.blogs') }}">Blog</a>
    <a href="{{ url_for('pages.home') }}#security">Security</a>
  </div>
  <div class="nav-actions">
    {% if session.get('user_id') %}
      <a href="{{ url_for('converter.dashboard') }}">Dashboard</a>
      <a class="btn-pill" href="{{ url_for('auth.account') }}">Account</a>
    {% else %}
      <a href="{{ url_for('auth.signin') }}">Sign in</a>
      <a class="btn-pill" href="{{ url_for('converter.dashboard') }}">Convert a PDF</a>
    {% endif %}
  </div>
</nav>
```

(`brand-word` renders in `var(--font-display)` 20px/600. If the current base.html nav uses different auth condition/endpoint names, keep the existing logic verbatim and only restyle.) Replace the footer with the 4-column editorial footer: brand column (logo mark + "Convert bank statement PDFs to clean, reviewable Excel files."), Product (Home/Pricing/Blog), Resources (How it works/Security/FAQ→pricing#faq), Legal (Privacy/Terms) — all via existing `url_for` targets — and bottom rule row: `© {{ current_year }} Statement Converter · A product of Ambion Softwares` + `Files are deleted after conversion.` Keep the flash-message block and `.alert` class names.

- [ ] **Step 4: Run suite**

Run: `python -m pytest tests/ -q`
Expected: all pass (157). If a test asserts old nav/footer strings, update that assertion in this commit.

- [ ] **Step 5: Commit**

```bash
git add static/styles.css static/logo-mark.svg templates/base.html
git commit -m "Rebuild design foundation: ledger tokens, nav, footer, logo"
```

---

### Task 2: home.html

**Files:**
- Modify: `templates/home.html` (full body rewrite inside existing blocks)
- Modify: `static/styles.css` (append home-section classes)
- Test: `tests/test_routes.py::TestPublicRoutes::test_home`

**Interfaces:**
- Consumes: Task 1 classes (`.nav` via base, `.display`, `.eyebrow`, `.btn-pill*`, `.hairline-grid`, `.spec-rows`, `.band-dark`, `.cta-band`, `.chip`, `.anim-float`).
- Produces: section ids `#workflow`, `#security` (nav links target them).

- [ ] **Step 1: Rewrite the content block** following mockup lines 46–226 with truth edits. Keep `{% block title %}`/meta blocks and all JSON-LD as-is. Structure (semantic; copy verbatim from here):
  - **Hero** (`.hero`, ink-deep band): badge pill "For accountants, auditors, founders & finance teams"; `<h1 class="display">Every statement, <em>rebuilt</em> as a workbook you can trust.</h1>` (em italic coral); sub "Upload a bank statement PDF — typed or scanned — and get back the same rows, columns, and balances, laid out in Excel exactly as they appeared."; CTAs: `.btn-pill--coral` "Convert a PDF free →" → `{{ url_for('converter.dashboard') }}`, underline link "See how it works" → `#workflow`. Trust stats (serif number + muted label): **1** "free conversion — no sign-up" / **5** "per month with a free account" / **Auto** "file expiry after processing".
  - **Transformation visual**: two rotated cards (`statement.pdf` dark card with 6 animated `rowFill` bars; `workbook.xlsx` cream card with 4×5 `gridGlow` cell grid, greens `#c9f4b0 #e4f7d8 #b4ff78 #d8f5b8` + ink every 5th) joined by a coral circle → arrow; mono caption "layout preserved · headers detected · balances aligned". Pure CSS/HTML, loops via Jinja: `{% for w in [55,88,70,92,60,80] %}`.
  - **Chip strip**: Text PDFs · Scanned PDFs · Multi-page tables · 500 MB uploads (Enterprise).
  - **Workflow** (`id="workflow"`): eyebrow "Workflow"; `<h2 class="display">One focused flow, from PDF to spreadsheet.</h2>`; intro from mockup line 136; 3 `.hairline-grid` cards with mockup copy (lines 260–262) — big ghost serif numerals 01/02/03.
  - **Accuracy band** (`.band-dark`): eyebrow (lime) "Accuracy direction"; h2 "Built around table geometry, not one bank's template."; 4 feature cards (mockup lines 266–269 copy: Dynamic headers / Text before OCR / Review context / Feedback loop) with color squares coral/lime/mist/gold.
  - **Security** (`id="security"`): eyebrow "Security"; h2 "Financial files deserve plain rules, not vague promises."; paragraph from mockup line 177; `.spec-rows`: "Uploaded PDFs → expire after processing" / "Excel outputs → removed after download window" / "File transfers → HTTPS".
  - **Final CTA** (`.cta-band`): h2 "Convert your next bank statement into Excel."; sub "1 free conversion as a guest — 5 a month with a free account. No credit card needed."; buttons "Convert a PDF free" (coral) + "View pricing" (ghost).

- [ ] **Step 2: Run home tests**

Run: `python -m pytest tests/test_routes.py -q -k "home"`
Expected: PASS (asserts `Statement Converter` + canonical — both still present).

- [ ] **Step 3: Full suite**

Run: `python -m pytest tests/ -q` — expected all green; fix any home-string assertions in this commit.

- [ ] **Step 4: Commit**

```bash
git add templates/home.html static/styles.css
git commit -m "Rebuild home page on ledger design system"
```

---

### Task 3: pricing.html (truth-first)

**Files:**
- Modify: `templates/pricing.html` (full body rewrite; keep FAQPage JSON-LD script block, updating its Q/A text to match visible copy)
- Modify: `static/styles.css` (plan-card styles)
- Test: `tests/test_routes.py::TestPublicRoutes::test_pricing`

**Interfaces:**
- Consumes: Task 1 classes; existing checkout endpoints (`billing.checkout_create` etc. — reuse the current template's exact form/link targets for each plan's CTA).
- Produces: `#faq` anchor (footer FAQ link targets it).

- [ ] **Step 1: Rewrite pricing body** per mockup `Pricing.dc.html` minus the yearly toggle:
  - Header: eyebrow "Pricing"; `<h1 class="display">Simple pricing for every volume of work.</h1>`; sub "Start free. Move up only when a real batch of statements needs it."
  - 3 plan cards (grid `repeat(3,1fr)`, 24px gap). **Free** (light `.card`): $0 /month, blurb "For occasional statements and trying things out.", features: 5 conversions / month · Text & scanned PDFs · 20 MB uploads · Full-text sheet included. **Pro** (featured: ink card, `transform:scale(1.03)`, "Most popular" coral badge): **$9.99** /month, blurb "For accountants and finance teams converting weekly.", features: 50 conversions / month · Text & scanned PDFs · 100 MB uploads · Priority email support. **Enterprise** (light): **$29.99** /month, blurb "For firms processing statements across many clients.", features: Unlimited conversions · 500 MB uploads · Priority email support · Custom statement-format support. CTA per card = the same auth/checkout links the current pricing.html uses (copy them verbatim).
  - FAQ (`id="faq"`): serif h2 "Questions, answered." + accordion of the current page's real FAQ entries restyled (native `<details>`/`<summary>` with coral `+` rotating via CSS `details[open] summary .faq-icon { transform: rotate(45deg); }`). Keep/update the FAQPage JSON-LD so it mirrors the visible Q/A exactly.
- [ ] **Step 2: Update the pricing test assertion** in `tests/test_routes.py::test_pricing`: replace `assert b"<h1>Simple, Transparent Pricing</h1>" in resp.data` with `assert b"Simple pricing for" in resp.data`. Keep `FAQPage` assertion.

- [ ] **Step 3: Run**

Run: `python -m pytest tests/ -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add templates/pricing.html static/styles.css tests/test_routes.py
git commit -m "Rebuild pricing page truth-first on ledger system"
```

---

### Task 4: signin.html + account.html

**Files:**
- Modify: `templates/signin.html`, `templates/account.html`
- Modify: `static/styles.css` (auth-card styles)
- Test: `tests/test_routes.py` (`test_signin` asserts lowercase "sign-in link" — keep that phrase in the copy)

**Interfaces:**
- Consumes: Task 1 classes; existing form actions (`auth.signin` POST, account's portal/signout endpoints) — reuse verbatim from current templates.

- [ ] **Step 1: signin.html** — centered `.card` (max-width 440px, 48px padding) on cream: logo mark, `<h1 class="display">Sign in with a magic link.</h1>`, sub "We'll email you a sign-in link — no password needed. Guests get 1 free conversion; a free account gets 5 a month."; email input + `.btn-pill` full-width submit "Email me a sign-in link". Keep the existing form action/method/CSRF and flash blocks.
- [ ] **Step 2: account.html** — `<h1 class="display">Your account.</h1>` + `.spec-rows` for: Email (mono), Plan, Conversions this month (mono `used / limit`), Upload limit (mono). Buttons: "Manage billing" (`.btn-pill`, existing portal endpoint), "Sign out" (ghost pill, existing endpoint). Preserve all Jinja variables the current template renders.

- [ ] **Step 3: Run**

Run: `python -m pytest tests/ -q` — expected green ("sign-in link" phrase preserved).

- [ ] **Step 4: Commit**

```bash
git add templates/signin.html templates/account.html static/styles.css
git commit -m "Restyle signin and account pages on ledger system"
```

---

### Task 5: dashboard.html + processing.html (JS-hook critical)

**Files:**
- Modify: `templates/dashboard.html`, `templates/processing.html`
- Modify: `static/styles.css` (dropzone, progress, result, modal styles)
- Test: `tests/test_routes.py` dashboard tests + manual flow

**Interfaces:**
- Consumes: Task 1 classes.
- **MUST PRESERVE (script.js contract)** — every one of these ids/classes/attrs, exact names, same element roles: ids `uploadForm, pdf_file, fileUploadArea, fileInfo, fileName, fileSize, removeFile, convertBtn, qualitySelector, retainInputPdf, progressModal, progressBar, progressFill, progressInfo, resultBanner, resultIcon, resultTitle, resultMessage, resultMeta, resultActions, resultClose, limitModal, limitTitle, limitMessage, limitClose, feedbackModal, feedbackForm, feedbackJobId, feedbackType, feedbackQuality, feedbackRows, feedbackCols, feedbackClose, feedbackSubmitBtn, downloadEmailModal, downloadEmailForm, downloadEmailJobId, downloadEmailFilename, downloadEmailClose, downloadEmailSubmitBtn`; classes `.btn-text, .loading-spinner, .main-content, .step-status, .upload-text, .progress-step, .alert`; attributes `data-quality="standard"`, `data-quality="high"`; state classes applied by JS: `active, completed, dragover, error, selected, success, warning` (style them in CSS); hidden CSRF input `input[name="csrf_token"]`.

- [ ] **Step 1: Inventory check** — before editing, run:

```bash
grep -oE "id=\"[a-zA-Z]+\"" templates/dashboard.html | sort -u
```

Diff against the preserve-list above; anything extra that script.js uses stays too.

- [ ] **Step 2: Rebuild dashboard.html visuals** around the preserved skeleton: `.main-content` cream shell; `<h1 class="display">Convert a statement.</h1>`; dropzone = `#fileUploadArea` restyled (2px dashed hairline, 20px radius, generous padding, coral border + cream-tint on `.dragover`, `.upload-text` muted, mono filename in `#fileName`); quality selector as two pill radio cards (`data-quality` attrs intact); convert button = `.btn-pill--coral` (keep `#convertBtn`, `.btn-text`, `.loading-spinner`); progress modal = spec-sheet rows + `.progress-step` items (ink dot → coral active → ok-green completed) + `#progressFill` coral bar; result banner styled as workbook card (ink header strip, mono meta in `#resultMeta`); modals = `.card` on scrim `rgba(14,20,32,.55)`. Keep every form action, SocketIO script include, and Jinja conditional exactly as-is.
- [ ] **Step 3: processing.html** — same spec-sheet + progress treatment; preserve any ids it declares (inventory first as in Step 1).
- [ ] **Step 4: Tests + manual flow**

Run: `python -m pytest tests/ -q` — green.
Then: `EXECUTION_PRESET=local-low-mem ./run_local.sh` and convert a small text PDF end-to-end (upload → progress → download modal) checking the browser console for JS errors; stop the server after.

- [ ] **Step 5: Commit**

```bash
git add templates/dashboard.html templates/processing.html static/styles.css
git commit -m "Restyle converter dashboard preserving script.js contract"
```

---

### Task 6: blogs.html + blog_post.html

**Files:**
- Modify: `templates/blogs.html`, `templates/blog_post.html`, `static/styles.css`
- Test: `tests/test_routes.py::test_blogs`, `test_blog_article`

**Interfaces:** Consumes Task 1 classes only.

- [ ] **Step 1: blogs.html** — keep `<h1>Blog and Guides</h1>` text (test asserts it) restyled as `.display`; article list = hairline-divided editorial rows (serif title, muted excerpt, mono date), preserving every existing post URL.
- [ ] **Step 2: blog_post.html** — article measure `max-width:70ch`; Newsreader h1/h2; Manrope body 17px/1.7; keep Article JSON-LD and existing heading text.
- [ ] **Step 3: Run** `python -m pytest tests/ -q` — green (h1 strings kept).
- [ ] **Step 4: Commit**

```bash
git add templates/blogs.html templates/blog_post.html static/styles.css
git commit -m "Restyle blog pages as editorial ledger layout"
```

---

### Task 7: privacy.html, terms.html, admin.html

**Files:**
- Modify: `templates/privacy.html`, `templates/terms.html`, `templates/admin.html`, `static/styles.css`
- Test: `test_privacy`, `test_terms`

**Interfaces:** Consumes Task 1 classes only.

- [ ] **Step 1: privacy + terms** — prose treatment: `.display` h1 (keep "Privacy Policy" / "Terms of Service" — tests assert), 70ch measure, hairline-ruled h2 sections. Content text unchanged.
- [ ] **Step 2: admin.html** — tokens only: Manrope, hairline table borders, ink header row, mono numerals. No structural changes.
- [ ] **Step 3: Run** `python -m pytest tests/ -q` — green.
- [ ] **Step 4: Commit**

```bash
git add templates/privacy.html templates/terms.html templates/admin.html static/styles.css
git commit -m "Restyle legal and admin pages"
```

---

### Task 8: Sweep + visual verification

**Files:**
- Modify: any file failing the checks below
- Test: full suite + browser pass

- [ ] **Step 1: Retirement greps** — all must return nothing:

```bash
grep -rn "Inter" templates/ static/styles.css
grep -rn "#000a63\|#046bca" templates/ static/
grep -rn "Ledger&Line\|Ledger&amp;Line" templates/ static/
grep -rn "ambion-logo" templates/   # replaced by logo-mark.svg (footer credit is text)
```

- [ ] **Step 2: Truth greps** — must return nothing:

```bash
grep -rni "batch\|team seats\|priority queue\|API access\|yearly" templates/pricing.html
```

- [ ] **Step 3: Full suite** — `python -m pytest tests/ -q` green.
- [ ] **Step 4: Visual pass** (browser tools or manual): home, pricing, signin, dashboard, blogs, account, privacy at desktop + 375px; check sticky nav blur, FAQ accordion keyboard toggle, hover states, `prefers-reduced-motion: reduce` (no animation), contrast spot-check on muted text.
- [ ] **Step 5: Commit any sweep fixes**

```bash
git add -A && git commit -m "Sweep: retire legacy brand references, polish responsive states"
```
