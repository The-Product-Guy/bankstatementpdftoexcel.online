# Launch SEO, GEO, AEO, and AIO Readiness

Use this checklist before pointing the production domain at Statement Converter.

## Required Production Variables

| Variable | Purpose |
| --- | --- |
| `PUBLIC_BASE_URL` or `CANONICAL_BASE_URL` | Absolute production origin used for canonical tags, social metadata, sitemap URLs, robots references, and LLM discovery files. Example: `https://statementconverter.com` |
| `GTM_CONTAINER_ID` or `GA_MEASUREMENT_ID` | Optional analytics container or GA4 measurement ID. |
| `SECURITY_CONTACT` | Optional public vulnerability contact for `/.well-known/security.txt`. Use `mailto:security@example.com` or an HTTPS policy/contact URL. |
| `INDEXNOW_KEY` | Optional IndexNow ownership key exposed at `/indexnow-key.txt` when configured. |

## Public Discovery URLs

| URL | Purpose |
| --- | --- |
| `/sitemap.xml` | XML sitemap with canonical public URLs and significant-update `lastmod` values. |
| `/sitemap.txt` | Plain text sitemap for crawlers and simple ingestion tools. |
| `/robots.txt` | Search and AI crawler policy with sitemap references. |
| `/llms.txt` | LLM-readable product summary and canonical guide links. |
| `/humans.txt` | Lightweight human-readable ownership and site summary. |
| `/.well-known/security.txt` | Vulnerability disclosure contact and policy metadata. |
| `/static/site.webmanifest` | Browser/PWA metadata and app icons. |

## Submit After Deploy

1. Verify `https://your-domain.com/robots.txt` references the production domain.
2. Verify `https://your-domain.com/sitemap.xml` contains only production canonical URLs.
3. Submit `https://your-domain.com/sitemap.xml` in Google Search Console.
4. Submit the sitemap in Bing Webmaster Tools.
5. If `INDEXNOW_KEY` is configured, submit updated URLs through IndexNow with `keyLocation=https://your-domain.com/indexnow-key.txt`.
6. Validate the home page and pricing page in Google's Rich Results Test.
7. Validate social previews with the Facebook Sharing Debugger and LinkedIn Post Inspector.

## Indexing Policy

Indexable pages are limited to the marketing, pricing, blog, privacy, and terms pages. Runtime and private application surfaces are excluded with `X-Robots-Tag: noindex, nofollow`, including dashboard, auth, conversion, status, download, billing, webhook, admin, and health-check routes.

## Answer-Engine Readiness

The app exposes visible, structured, and consistent answers through:

- page-specific titles and meta descriptions
- environment-driven canonical URLs
- Open Graph and Twitter preview images
- `WebApplication`, `Organization`, `WebSite`, `Blog`, `Article`, `Service`, `OfferCatalog`, `BreadcrumbList`, and visible `FAQPage` JSON-LD
- `llms.txt` with concise product facts, limitations, policy links, and guide URLs
- plain English visible content that describes supported inputs, outputs, retention, limitations, and review expectations

## Preflight Commands

```bash
python -m pytest tests/test_routes.py
python -m pytest tests/test_tracking.py
```
