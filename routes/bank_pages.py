"""Data for /convert/<bank> landing pages.

Each entry describes how that bank actually formats statements — columns,
date style, quirks — so every page carries genuinely distinct, truthful
content. Honesty rules (see docs/superpowers/specs/2026-07-08-seo-round-design.md):
no "official"/"partner"/"supported bank" claims, no accuracy percentages or
lossless-conversion promises. Exact_Copy preserves all successfully extracted
words and visual rows with approximate geometry. Table_Data is best effort,
and OCR results require review against the PDF.
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
            "page geometry printed in each PDF. Exact_Copy retains the extracted "
            "visual rows, while Table_Data makes a best-effort attempt to organise "
            "detected transactions under the printed headings."
        ),
        "faqs": [
            {"q": "Does this work with Chase credit card statements as well as checking?",
             "a": "The parser reads page geometry rather than a fixed Chase template, so it can process checking, savings, and card layouts. Review the resulting rows and columns against your PDF."},
            {"q": "What will the Chase Excel workbook contain?",
             "a": "Exact_Copy retains every successfully extracted word in visual row order with approximate page geometry. Table_Data is a best-effort transaction view and may need row or column cleanup."},
            {"q": "Do I need to tell the converter which bank the PDF is from?",
             "a": "No. The converter reads the document itself; any Table_Data columns are inferred from that page rather than selected from a Chase template."},
            {"q": "Is a scanned Chase statement supported?",
             "a": "Scanned pages route through OCR before layout placement. OCR can miss or misread text, so compare dates, amounts, rows, and columns with the source PDF."},
        ],
        "lastmod": "2026-08-09",
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
            "description, and amount. Exact_Copy retains the extracted section "
            "headings and visual rows with approximate page placement. Table_Data "
            "then attempts to organise detected transactions for easier cleanup."
        ),
        "faqs": [
            {"q": "How are the separate deposits and withdrawals sections handled?",
             "a": "Extracted section headings and daily-balance blocks remain visible in Exact_Copy. Table_Data is a best-effort transaction view, so check which section each row belongs to before analysis."},
            {"q": "Do MM/DD/YY dates come through correctly?",
             "a": "Extracted dates are written as text rather than intentionally converted to another date format. Check OCR characters and ambiguous dates against the PDF."},
            {"q": "Can I convert both bank and card statements?",
             "a": "The converter reads each document's page geometry rather than a per-product template, so it can process both. Review each workbook because their layouts differ."},
        ],
        "lastmod": "2026-08-09",
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
            "and an ending daily balance that only prints on the last transaction "
            "of each day. Exact_Copy retains the extracted visual rows and blank "
            "spacing. Table_Data attempts to assign those values to detected "
            "columns and should be checked against the PDF."
        ),
        "faqs": [
            {"q": "The balance column is empty on most rows of my statement — is that kept?",
             "a": "Exact_Copy retains the extracted visual placement, including blank space around daily balances. Check Table_Data against it and the PDF because automated column assignment can move or omit a value."},
            {"q": "Are check numbers kept in their own column?",
             "a": "Table_Data attempts to detect the printed Number column separately from Description. Exact_Copy keeps the extracted words and positions so you can verify that split."},
            {"q": "What about long descriptions that wrap to a second line?",
             "a": "Exact_Copy keeps wrapped lines in visual order. Table_Data may combine them with a detected transaction row, but review row boundaries before counting or summing."},
        ],
        "lastmod": "2026-08-09",
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
            "shapes are read from the page geometry. Exact_Copy retains the "
            "successfully extracted visual rows; Table_Data attempts to identify "
            "the relevant transaction columns for that document."
        ),
        "faqs": [
            {"q": "My card statement has both a sale date and a post date — are both kept?",
             "a": "Both extracted dates remain visible in Exact_Copy. Table_Data attempts to place them under separate detected headings; verify the split before using the dates."},
            {"q": "Are debits and credits kept apart?",
             "a": "Exact_Copy preserves their extracted page positions. Table_Data attempts to keep printed Debit and Credit columns separate, but you should review the column assignment."},
            {"q": "Does a scanned Citi statement work?",
             "a": "Scans route through OCR before layout placement. OCR may misread amounts, dates, or headings, so compare both workbook views with the statement."},
        ],
        "lastmod": "2026-08-09",
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
            "balance layout. Exact_Copy retains successfully extracted dates and "
            "visual rows; Table_Data makes a best-effort attempt to recognise "
            "month-name dates and separate transaction columns."
        ),
        "faqs": [
            {"q": "Do Trans Date and Post Date both come through?",
             "a": "Both successfully extracted dates remain in Exact_Copy. Table_Data attempts to place them in separate columns under the detected headings; check the result against the PDF."},
            {"q": "Are month-name dates like \"Jun 3\" a problem?",
             "a": "They can be read from the printed text, but Table_Data row classification is best effort. Confirm the date text and its row in Exact_Copy and the PDF."},
            {"q": "Does this cover Capital One 360 checking as well as cards?",
             "a": "The converter reads each document's page geometry rather than a per-product template, so it can process both. Their different layouts should be reviewed separately."},
        ],
        "lastmod": "2026-08-09",
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
            "3 Jun 2026. Long payee references wrap onto continuation lines. "
            "Exact_Copy retains those extracted lines in visual order, while "
            "Table_Data makes a best-effort attempt to combine them with the "
            "detected transaction."
        ),
        "faqs": [
            {"q": "Are Money out and Money in kept as separate columns?",
             "a": "Exact_Copy retains their extracted words and page positions. Table_Data attempts to keep Money out and Money in under separate detected headings; verify the assignment before summing."},
            {"q": "What about wrapped payment references?",
             "a": "Exact_Copy keeps continuation lines in visual order. Table_Data may merge them with a detected transaction row, but review the row boundaries against the statement."},
            {"q": "Does it handle £ amounts and UK dates?",
             "a": "Successfully extracted values are written as text, and Table_Data attempts to recognise D Mon YYYY rows. Check symbols, digits, and dates against the PDF, especially after OCR."},
        ],
        "lastmod": "2026-08-09",
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
            "wraps, Exact_Copy retains its extracted visual lines, and Table_Data "
            "attempts to associate continuation text with a detected transaction."
        ),
        "faqs": [
            {"q": "Are HSBC's payment-type codes (DD, VIS, BP) preserved?",
             "a": "Successfully extracted codes remain visible in Exact_Copy without intentional rewriting. Check OCR characters and any Table_Data grouping against the PDF."},
            {"q": "Paid out and Paid in stay separate?",
             "a": "Table_Data attempts to keep Paid out and Paid in under separate detected headings. Exact_Copy retains the extracted positions so you can verify amounts and £ symbols."},
            {"q": "My statement is a scan from a branch printer — will it work?",
             "a": "Scanned pages go through OCR before layout placement. OCR can miss or confuse text, so use the clearest scan available and review every important row and column."},
        ],
        "lastmod": "2026-08-09",
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
            "in each cell. Exact_Copy retains the extracted page structure, while "
            "Table_Data attempts to reproduce that useful column split."
        ),
        "faqs": [
            {"q": "Is the payment-type column kept separate from the details?",
             "a": "Table_Data attempts to detect Payment type separately from Details. Confirm that split in Exact_Copy and the PDF before filtering."},
            {"q": "Do the (£) headings come through?",
             "a": "Successfully extracted headings and currency markers remain visible in Exact_Copy. OCR can misread small symbols, so check scanned statements carefully."},
            {"q": "How are wrapped payee references handled?",
             "a": "Exact_Copy keeps continuation lines in visual order. Table_Data may join them to a detected transaction, but row merging is best effort and should be reviewed."},
        ],
        "lastmod": "2026-08-09",
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
            "running balance. Dates repeat only when the day changes. Exact_Copy "
            "retains the extracted visual rows and spacing, while Table_Data "
            "attempts to organise values under the detected headings."
        ),
        "faqs": [
            {"q": "NatWest only prints the date when it changes — is that preserved?",
             "a": "Exact_Copy retains the extracted visual placement, including the gap where no date was printed. Check Table_Data because automated row assembly may handle repeated-date groups differently."},
            {"q": "Are Paid in and Withdrawn kept as separate columns?",
             "a": "Table_Data attempts to keep Paid in and Withdrawn under separate detected headings. Verify each amount column against Exact_Copy and the PDF."},
            {"q": "Can I convert several months of statements?",
             "a": "Each PDF converts as its own job. Exact_Copy preserves successfully extracted visual rows page by page; review page transitions and repeated headers."},
        ],
        "lastmod": "2026-08-09",
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
            "amounts like 1.234,56. Exact_Copy retains the successfully extracted "
            "Dutch text and visual positions without intentionally converting the "
            "number format. Table_Data attempts to detect the transaction columns."
        ),
        "faqs": [
            {"q": "Are Dutch headings like Af and Bij recognised?",
             "a": "Extracted headings such as Af and Bij remain visible in Exact_Copy. Table_Data attempts to use the detected headings for its columns; verify them against the PDF."},
            {"q": "Do decimal-comma amounts (1.234,56) survive?",
             "a": "Successfully extracted amounts are written as text without intentional US-style conversion. Check digits and separators, then decide in Excel how to parse them."},
            {"q": "Does it work for both betaalrekening and spaarrekening statements?",
             "a": "The converter reads each document's geometry rather than an account-type template, so it can process both. Review their different layouts separately."},
        ],
        "lastmod": "2026-08-09",
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
            "formatting. Exact_Copy retains the extracted visual lines and "
            "positions. Table_Data attempts to combine wrapped purpose text and "
            "identify the Soll and Haben columns."
        ),
        "faqs": [
            {"q": "How is a multi-line Verwendungszweck handled?",
             "a": "Exact_Copy keeps all successfully extracted continuation lines in visual order. Table_Data may combine them with a transaction row, but review that best-effort merge."},
            {"q": "Are Buchungstag and Wert both kept?",
             "a": "Both successfully extracted headings and dates remain visible in Exact_Copy. Table_Data attempts to keep them in separate columns; confirm the split against the PDF."},
            {"q": "Do DD.MM.YYYY dates and 1.234,56 amounts survive?",
             "a": "Extracted values are written as text without intentional locale conversion. Review OCR characters and Table_Data row assembly before parsing them in Excel."},
        ],
        "lastmod": "2026-08-09",
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
            "from the printed page. Exact_Copy retains the extracted text and "
            "visual geometry; Table_Data attempts to organise detected columns."
        ),
        "faqs": [
            {"q": "Does this handle both UK and Spanish Santander statements?",
             "a": "The converter reads headings and geometry from the document rather than a language-specific template, so it can process both. Review each result against its source PDF."},
            {"q": "Are DD/MM/YYYY dates kept without being reinterpreted?",
             "a": "Successfully extracted dates are written as text without intentional US-style conversion. Confirm ambiguous dates and OCR characters against the PDF."},
            {"q": "What happens to transaction references that wrap?",
             "a": "Exact_Copy keeps wrapped lines in visual order. Table_Data may merge them into a detected transaction row, but check the row boundary before using it."},
        ],
        "lastmod": "2026-08-09",
    },
]

BANK_BY_SLUG = {bank["slug"]: bank for bank in BANK_PAGES}
