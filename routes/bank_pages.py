"""Data for /convert/<bank> landing pages.

Each entry describes how that bank actually formats statements — columns,
date style, quirks — so every page carries genuinely distinct, truthful
content. Honesty rules (see docs/superpowers/specs/2026-07-08-seo-round-design.md):
no "official"/"partner"/"supported bank" claims, no accuracy percentages.
The parser is universal (geometry-first); these pages describe formats, not
integrations.
"""

BANK_PAGES = [
    # ---------------- United States ----------------
    {
        "slug": "chase-statement-to-excel",
        "name": "Chase",
        "country": "United States",
        "currency": "USD ($)",
        "date_format": "MM/DD or MM/DD/YYYY",
        "columns": ["Date", "Description", "Amount", "Balance"],
        "layout_notes": (
            "Chase checking and savings statements list transactions under a "
            "summary block, with deposits and withdrawals in a signed amount "
            "column and a running balance. Card statements group purchases by "
            "billing cycle with short MM/DD dates. The converter reads the "
            "column positions printed on your PDF, so either layout comes out "
            "under Chase's own headings — one Excel row per printed transaction."
        ),
        "faqs": [
            {"q": "Does this work with Chase credit card statements as well as checking?",
             "a": "Yes. The parser reads the table geometry printed on the page rather than a fixed template, so checking, savings, and card statements each come out with their own printed columns."},
            {"q": "Will the Excel match my Chase statement exactly?",
             "a": "The first sheet mirrors the statement's own table — Chase's column headings, one row per transaction, amounts kept as printed text. A Full_Text sheet carries every other line on the statement so nothing is dropped."},
            {"q": "Do I need to tell the converter which bank the PDF is from?",
             "a": "No. Column layout is detected from the document itself."},
            {"q": "Is a scanned Chase statement supported?",
             "a": "Yes — scanned pages route through OCR and the same layout reconstruction. Very poor scans are flagged so you can retry in high quality."},
        ],
        "lastmod": "2026-07-08",
    },
    {
        "slug": "bank-of-america-statement-to-excel",
        "name": "Bank of America",
        "country": "United States",
        "currency": "USD ($)",
        "date_format": "MM/DD/YY",
        "columns": ["Date", "Description", "Amount"],
        "layout_notes": (
            "Bank of America statements split activity into separate sections — "
            "\"Deposits and other additions\" and \"Withdrawals and other "
            "subtractions\" — each printed as its own small table of date, "
            "description, and amount. The converter rebuilds each printed table "
            "as it appears, and the Full_Text sheet keeps the section headings, "
            "so you can see exactly which block a row came from."
        ),
        "faqs": [
            {"q": "How are the separate deposits and withdrawals sections handled?",
             "a": "Each printed section is reconstructed as printed. Section headings and daily-balance blocks are preserved in the Full_Text sheet, so nothing between the tables is lost."},
            {"q": "Do MM/DD/YY dates come through correctly?",
             "a": "Yes — dates are kept exactly as printed text, so 06/03/26 stays 06/03/26 rather than being reinterpreted."},
            {"q": "Can I convert both bank and card statements?",
             "a": "Yes. Layouts are detected per document from the printed columns, not from a per-product template."},
        ],
        "lastmod": "2026-07-08",
    },
    {
        "slug": "wells-fargo-statement-to-excel",
        "name": "Wells Fargo",
        "country": "United States",
        "currency": "USD ($)",
        "date_format": "MM/DD",
        "columns": ["Date", "Number", "Description", "Deposits/Credits", "Withdrawals/Debits", "Ending daily balance"],
        "layout_notes": (
            "Wells Fargo's transaction history is one wide table: date, check "
            "number, description, separate deposits and withdrawals columns, "
            "and an ending daily balance that only prints on the last "
            "transaction of each day. The converter keeps those blanks blank — "
            "cells are never invented — so the Excel reads exactly like the "
            "printed history."
        ),
        "faqs": [
            {"q": "The balance column is empty on most rows of my statement — is that kept?",
             "a": "Yes. Wells Fargo prints the ending daily balance once per day, and the Excel mirrors that: the balance cell is filled only where the statement fills it."},
            {"q": "Are check numbers kept in their own column?",
             "a": "Yes — the Number column is detected from the printed header row and stays separate from the description."},
            {"q": "What about long descriptions that wrap to a second line?",
             "a": "Wrapped description lines are merged back into their transaction's row, so row counts match the statement."},
        ],
        "lastmod": "2026-07-08",
    },
    {
        "slug": "citi-statement-to-excel",
        "name": "Citi",
        "country": "United States",
        "currency": "USD ($)",
        "date_format": "MM/DD",
        "columns": ["Date", "Description", "Debits", "Credits", "Balance"],
        "layout_notes": (
            "Citibank checking statements use separate debit and credit columns "
            "with a running balance, while Citi card statements print sale and "
            "post dates side by side with a single signed amount column. Both "
            "shapes are read from the page geometry — the Excel keeps whichever "
            "column set your document actually uses."
        ),
        "faqs": [
            {"q": "My card statement has both a sale date and a post date — are both kept?",
             "a": "Yes. Every printed column becomes an Excel column under its own heading; neither date is discarded or merged."},
            {"q": "Are debits and credits kept apart?",
             "a": "Yes — when the statement prints separate Debits and Credits columns, the workbook keeps them separate, exactly as printed."},
            {"q": "Does a scanned Citi statement work?",
             "a": "Yes. Scans route through OCR into the same layout reconstruction, and poor-quality pages are flagged for a high-quality retry."},
        ],
        "lastmod": "2026-07-08",
    },
    {
        "slug": "capital-one-statement-to-excel",
        "name": "Capital One",
        "country": "United States",
        "currency": "USD ($)",
        "date_format": "MMM DD or MM/DD",
        "columns": ["Trans Date", "Post Date", "Description", "Amount"],
        "layout_notes": (
            "Capital One card statements print a transaction date and a posting "
            "date before each description, with a single signed amount column; "
            "360 banking statements use a simpler date/description/amount/"
            "balance layout. Month-name dates like \"Jun 3\" are recognized "
            "when rows are assembled, and both date columns survive into Excel."
        ),
        "faqs": [
            {"q": "Do Trans Date and Post Date both come through?",
             "a": "Yes — every printed column becomes its own Excel column under the statement's own heading."},
            {"q": "Are month-name dates like \"Jun 3\" a problem?",
             "a": "No. Rows are recognized by the printed table geometry, and month-name dates are treated as dates when rows are classified."},
            {"q": "Does this cover Capital One 360 checking as well as cards?",
             "a": "Yes. The layout is detected from each document's printed columns rather than a per-product template."},
        ],
        "lastmod": "2026-07-08",
    },
    # ---------------- United Kingdom ----------------
    {
        "slug": "barclays-statement-to-excel",
        "name": "Barclays",
        "country": "United Kingdom",
        "currency": "GBP (£)",
        "date_format": "D Mon YYYY (e.g. 3 Jun 2026)",
        "columns": ["Date", "Description", "Money out", "Money in", "Balance"],
        "layout_notes": (
            "Barclays current-account statements use separate Money out and "
            "Money in columns with a running balance, and spell dates like "
            "3 Jun 2026. Long payee references wrap onto continuation lines — "
            "the converter merges those back into one row, so a wrapped Direct "
            "Debit reference doesn't become two transactions in Excel."
        ),
        "faqs": [
            {"q": "Are Money out and Money in kept as separate columns?",
             "a": "Yes — the Excel uses the statement's own printed headings, so Money out and Money in stay separate exactly as Barclays prints them."},
            {"q": "What about wrapped payment references?",
             "a": "Description lines that wrap over several printed lines are merged into their parent transaction's row, so row counts match your statement."},
            {"q": "Does it handle £ amounts and UK dates?",
             "a": "Yes. Values are kept as printed text — £1,234.56 stays £1,234.56 — and D Mon YYYY dates are recognised when rows are assembled."},
        ],
        "lastmod": "2026-07-08",
    },
    {
        "slug": "hsbc-statement-to-excel",
        "name": "HSBC",
        "country": "United Kingdom",
        "currency": "GBP (£)",
        "date_format": "DD Mon YY",
        "columns": ["Date", "Payment type and details", "Paid out", "Paid in", "Balance"],
        "layout_notes": (
            "HSBC UK statements combine the payment type (VIS, DD, BP, TFR) and "
            "the payee into one wide details column, flanked by Paid out and "
            "Paid in columns and a balance. Because that details column often "
            "wraps, the converter folds continuation lines back into the row "
            "they belong to and keeps the type codes exactly as printed."
        ),
        "faqs": [
            {"q": "Are HSBC's payment-type codes (DD, VIS, BP) preserved?",
             "a": "Yes — the details column is copied as printed, codes included. Nothing is reworded or normalised."},
            {"q": "Paid out and Paid in stay separate?",
             "a": "Yes, under HSBC's own printed headings, with amounts kept as text so £ signs and formatting survive."},
            {"q": "My statement is a scan from a branch printer — will it work?",
             "a": "Scanned pages go through OCR into the same layout reconstruction. Poor scans are flagged so you can retry in high quality."},
        ],
        "lastmod": "2026-07-08",
    },
    {
        "slug": "lloyds-statement-to-excel",
        "name": "Lloyds",
        "country": "United Kingdom",
        "currency": "GBP (£)",
        "date_format": "DD Mon YY",
        "columns": ["Date", "Payment type", "Details", "Money Out (£)", "Money In (£)", "Balance (£)"],
        "layout_notes": (
            "Lloyds statements put the payment type (DEB, DD, FPI, SO) in its "
            "own narrow column next to the details, then Money Out, Money In, "
            "and Balance — all with the pound sign in the heading rather than "
            "in each cell. The converter keeps that exact column split, so you "
            "can filter by payment type in Excel immediately."
        ),
        "faqs": [
            {"q": "Is the payment-type column kept separate from the details?",
             "a": "Yes — Lloyds prints them as separate columns and the Excel keeps them separate, which makes type-based filtering easy."},
            {"q": "Do the (£) headings come through?",
             "a": "Yes. Headings are copied as printed, including the currency marker."},
            {"q": "How are wrapped payee references handled?",
             "a": "Continuation lines are merged into their transaction's row, so a long reference stays one transaction."},
        ],
        "lastmod": "2026-07-08",
    },
    {
        "slug": "natwest-statement-to-excel",
        "name": "NatWest",
        "country": "United Kingdom",
        "currency": "GBP (£)",
        "date_format": "DD Mon YYYY",
        "columns": ["Date", "Type", "Description", "Paid in", "Withdrawn", "Balance"],
        "layout_notes": (
            "NatWest statements print a short type column (BAC, D/D, CHQ, POS) "
            "before the description, with Paid in and Withdrawn columns and a "
            "running balance. Dates repeat only when the day changes — the "
            "converter keeps those blanks as printed rather than filling them "
            "in, so the Excel stays a faithful copy you can audit."
        ),
        "faqs": [
            {"q": "NatWest only prints the date when it changes — is that preserved?",
             "a": "Yes. Cells are never invented: rows without a printed date keep an empty date cell, exactly like the statement."},
            {"q": "Are Paid in and Withdrawn kept as separate columns?",
             "a": "Yes, under the statement's own printed headings."},
            {"q": "Can I convert several months of statements?",
             "a": "Yes — each PDF converts on its own, and multi-page documents keep their layout page by page."},
        ],
        "lastmod": "2026-07-08",
    },
    # ---------------- Europe ----------------
    {
        "slug": "ing-statement-to-excel",
        "name": "ING",
        "country": "Netherlands",
        "currency": "EUR (€)",
        "date_format": "DD-MM-YYYY",
        "columns": ["Datum", "Naam / Omschrijving", "Af", "Bij", "Saldo"],
        "layout_notes": (
            "ING afschriften use Dutch column headings — Datum, Naam/"
            "Omschrijving, Af, Bij — with DD-MM-YYYY dates and decimal-comma "
            "amounts like 1.234,56. The converter recognises the Dutch headings "
            "and the European number format, and keeps both exactly as printed: "
            "no reformatting into US-style numbers."
        ),
        "faqs": [
            {"q": "Are Dutch headings like Af and Bij recognised?",
             "a": "Yes — the header row is detected from the document, so the Excel columns are titled Datum, Omschrijving, Af, Bij, Saldo, exactly as ING prints them."},
            {"q": "Do decimal-comma amounts (1.234,56) survive?",
             "a": "Yes. Amounts are kept as printed text, so European formatting is untouched — you decide in Excel how to parse them."},
            {"q": "Does it work for both betaalrekening and spaarrekening statements?",
             "a": "Yes. The layout is read from each document's printed columns, not from an account-type template."},
        ],
        "lastmod": "2026-07-08",
    },
    {
        "slug": "deutsche-bank-statement-to-excel",
        "name": "Deutsche Bank",
        "country": "Germany",
        "currency": "EUR (€)",
        "date_format": "DD.MM.YYYY",
        "columns": ["Buchungstag", "Wert", "Verwendungszweck", "Soll", "Haben"],
        "layout_notes": (
            "Deutsche Bank Kontoauszüge print a booking date and value date, a "
            "Verwendungszweck column that regularly wraps over several lines, "
            "and separate Soll and Haben amount columns with German number "
            "formatting. The converter merges the wrapped Verwendungszweck back "
            "into one row and recognises Soll/Haben as amount columns."
        ),
        "faqs": [
            {"q": "Is the multi-line Verwendungszweck merged correctly?",
             "a": "Yes — continuation lines are folded into their transaction's row, so a long reference stays one row in Excel."},
            {"q": "Are Buchungstag and Wert both kept?",
             "a": "Yes. Every printed column becomes its own Excel column under the German heading."},
            {"q": "Do DD.MM.YYYY dates and 1.234,56 amounts survive?",
             "a": "Yes — both are recognised during row assembly and copied as printed text, without reformatting."},
        ],
        "lastmod": "2026-07-08",
    },
    {
        "slug": "santander-statement-to-excel",
        "name": "Santander",
        "country": "United Kingdom & Spain",
        "currency": "GBP (£) / EUR (€)",
        "date_format": "DD/MM/YYYY",
        "columns": ["Date", "Description", "Money in", "Money out", "Balance"],
        "layout_notes": (
            "Santander UK statements use Money in / Money out columns with "
            "DD/MM/YYYY dates, while Spanish extractos print Fecha, Concepto, "
            "Importe and Saldo with decimal-comma amounts. Both shapes are read "
            "from the printed page — the Excel keeps whichever headings and "
            "number format your statement actually uses."
        ),
        "faqs": [
            {"q": "Does this handle both UK and Spanish Santander statements?",
             "a": "Yes. The header row and columns are detected from the document itself, so English and Spanish headings each come through as printed."},
            {"q": "Are DD/MM/YYYY dates kept without being reinterpreted?",
             "a": "Yes — dates stay text, exactly as printed, so 03/06/2026 is never silently flipped into a US-style date."},
            {"q": "What happens to transaction references that wrap?",
             "a": "Wrapped lines merge into their parent row, keeping one row per transaction."},
        ],
        "lastmod": "2026-07-08",
    },
]

BANK_BY_SLUG = {bank["slug"]: bank for bank in BANK_PAGES}
