# Exact-Copy Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the default `layout_replica` Excel output an exact copy of the PDF — one Excel row per PDF table row, nothing silently deleted — plus 4 marketing honesty fixes.

**Architecture:** All parser changes live in `parsers/layout_replica_parser.py` (continuation-merge inside `_table_rows_from_page`, plus writing the existing-but-unused lines sheet in `write_excel`). Marketing fixes are copy edits in 3 templates and a file-retention fix in `static/script.js`. No worker/route/model changes.

**Tech Stack:** Python 3.11, pdfplumber, openpyxl, pytest (+reportlab for test PDFs), vanilla JS.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-03-exact-copy-extraction-design.md`
- All Excel cell values remain strings (`number_format = "@"`).
- `write_excel(output_path)` signature unchanged (worker.py depends on it).
- `structured_transactions` mode untouched.
- Test command: `./venv/bin/python -m pytest tests/ -v` from repo root.
- Sample PDFs for verification (scanned, need OCR): `KVB-CA-PART-02- 01.07.2018 TO 31.03.2019.pdf`, `KVB-CA-PART-03- 01.04.2019 TO 31.03.2020.pdf` at repo root.

---

### Task 1: Continuation merge — one Excel row per PDF table row

**Files:**
- Modify: `parsers/layout_replica_parser.py` (`_table_rows_from_page`, ~line 597; new helpers after `_join_words`, ~line 919)
- Test: `tests/test_layout_replica_parser.py`

**Interfaces:**
- Produces: `LayoutReplicaParser._is_continuation_row(line: LayoutLine, values: List[str], columns: List[TableColumn], page_rows: List[TableReplicaRow]) -> bool`; `LayoutReplicaParser._merge_continuation_row(parent: TableReplicaRow, values: List[str]) -> None` (static); `_is_text_column(column: TableColumn, idx: int) -> bool`. `_table_rows_from_page` now merges continuation lines into the previous row instead of emitting orphan rows.

- [ ] **Step 1: Update the two existing tests that lock the old split-row behavior, and add two new merge tests**

In `tests/test_layout_replica_parser.py`:

(a) In `test_table_replica_keeps_rows_before_repeated_header_on_same_pdf_page`, the page-2 continuation line `684155000075893` (x 143.2–217.0 = DESCRIPTION column) must now merge into the MPAY row. Replace the final assertion block:

```python
    page_two_rows = [
        row.values for row in parser.table_rows
        if row.page == 2
    ]
    assert [
        "13/04/19",
        "13/04/19",
        "1763",
        "MPAY/UPI/FI Funds Trans-1 684155000075893",
        "772108174541",
        "8,600.00",
        "",
        "42,557.10",
    ] in page_two_rows
    assert not any("STATEMENT OF ACCOUNT" in " ".join(row) for row in page_two_rows)
    assert not any("Account Number" in " ".join(row) for row in page_two_rows)
    # continuation line must no longer be its own row
    assert not any(row[0] == "" and "684155000075893" in " ".join(row) for row in page_two_rows)
```

(b) In `test_table_replica_infers_columns_when_header_is_missing`, the `PTY-...` continuation merges into the first row; the context line stays dropped. Replace the tail assertions (`parser.table_rows[0].values == [...]` through `len(parser.table_rows) == 2`) with:

```python
    assert parser.table_rows[0].values == [
        "29/01/25",
        "UPI-SELVI PTY-YESB0MCHUPI-024036557328-PAYMENT",
        "0000024036557328",
        "29/01/25",
        "72.00",
        "0.00",
        "1,523.00",
    ]
    assert len(parser.table_rows) == 1
```

(c) Append two new tests at the end of the file (reuse the module-level helper style; define local `make_line`/`make_page` helpers exactly as in `test_table_replica_keeps_rows_before_repeated_header_on_same_pdf_page`):

```python
def test_continuation_merges_into_description_and_reference_columns():
    from parsers.layout_replica_parser import LayoutLine, LayoutPage, LayoutReplicaParser, LayoutWord

    def make_line(page_num, index, words):
        layout_words = [
            LayoutWord(text=text, x0=x0, x1=x1, top=index * 10.0,
                       bottom=index * 10.0 + 8.0, page=page_num, source="pdf-text")
            for text, x0, x1 in words
        ]
        return LayoutLine(page=page_num, index=index, top=index * 10.0,
                          bottom=index * 10.0 + 8.0, center_y=index * 10.0 + 4.0,
                          words=layout_words,
                          text=" ".join(word.text for word in layout_words))

    separator = [("-" * 100, 16.6, 597.2)]
    header = [
        ("TXN", 16.6, 32.4), ("DT", 37.7, 48.2), ("VALUE_DT", 64.1, 106.3),
        ("BRN", 116.9, 132.7), ("DESCRIPTION", 169.6, 227.7),
        ("REFERENCE", 280.5, 328.0), ("DEBITS", 380.8, 412.5),
        ("CREDITS", 454.7, 491.6), ("BALANCE", 560.3, 597.2),
    ]
    lines = [
        make_line(1, 1, separator),
        make_line(1, 2, header),
        make_line(1, 3, separator),
        make_line(1, 4, [
            ("03/04/19", 16.6, 58.8), ("03/04/19", 64.1, 106.3), ("1763", 111.6, 132.7),
            ("IMPS", 143.2, 168.0), ("DR-1763308", 172.0, 227.0),
            ("909223247145", 280.5, 343.8), ("5,000.00", 375.5, 423.0),
            ("1,66,685.70", 533.9, 591.9),
        ]),
        # wraps into DESCRIPTION and REFERENCE columns, no date, no amounts
        make_line(1, 5, [("HDFC0000240-3017FA", 143.2, 230.0), ("835", 280.5, 300.0)]),
    ]
    parser = LayoutReplicaParser(use_ocr=False)
    parser.pages = [LayoutPage(page_number=1, width=620, height=800, source="pdf-text",
                               words=[w for line in lines for w in line.words], lines=lines)]
    parser._build_table_replica()

    assert len(parser.table_rows) == 1
    # 8 columns: "TXN" and "DT" merge into one header cell, so
    # idx 3 = DESCRIPTION, idx 4 = REFERENCE, idx 5 = DEBITS
    row = parser.table_rows[0].values
    assert row[3] == "IMPS DR-1763308 HDFC0000240-3017FA"
    assert row[4] == "909223247145 835"
    assert row[5] == "5,000.00"


def test_date_starting_line_is_never_merged_as_continuation():
    from parsers.layout_replica_parser import LayoutLine, LayoutPage, LayoutReplicaParser, LayoutWord

    def make_line(page_num, index, words):
        layout_words = [
            LayoutWord(text=text, x0=x0, x1=x1, top=index * 10.0,
                       bottom=index * 10.0 + 8.0, page=page_num, source="pdf-text")
            for text, x0, x1 in words
        ]
        return LayoutLine(page=page_num, index=index, top=index * 10.0,
                          bottom=index * 10.0 + 8.0, center_y=index * 10.0 + 4.0,
                          words=layout_words,
                          text=" ".join(word.text for word in layout_words))

    separator = [("-" * 100, 16.6, 597.2)]
    header = [
        ("TXN", 16.6, 32.4), ("DT", 37.7, 48.2), ("VALUE_DT", 64.1, 106.3),
        ("BRN", 116.9, 132.7), ("DESCRIPTION", 169.6, 227.7),
        ("REFERENCE", 280.5, 328.0), ("DEBITS", 380.8, 412.5),
        ("CREDITS", 454.7, 491.6), ("BALANCE", 560.3, 597.2),
    ]
    lines = [
        make_line(1, 1, separator),
        make_line(1, 2, header),
        make_line(1, 3, separator),
        make_line(1, 4, [
            ("01/04/19", 16.6, 58.8), ("01/04/19", 64.1, 106.3), ("1763", 111.6, 132.7),
            ("ATM", 143.2, 162.0), ("CSW", 166.0, 190.0),
            ("909110624882", 280.5, 343.8), ("10,000.00", 375.5, 423.0),
            ("1,43,265.70", 533.9, 591.9),
        ]),
        make_line(1, 5, [
            ("01/04/19", 16.6, 58.8), ("01/04/19", 64.1, 106.3), ("1763", 111.6, 132.7),
            ("CA", 143.2, 155.0), ("ATM", 159.0, 178.0), ("TXN", 182.0, 200.0),
            ("909110624882", 280.5, 343.8), ("20.00", 375.5, 423.0),
            ("1,43,245.70", 533.9, 591.9),
        ]),
    ]
    parser = LayoutReplicaParser(use_ocr=False)
    parser.pages = [LayoutPage(page_number=1, width=620, height=800, source="pdf-text",
                               words=[w for line in lines for w in line.words], lines=lines)]
    parser._build_table_replica()

    assert len(parser.table_rows) == 2
    assert parser.table_rows[0].values[5] == "10,000.00"
    assert parser.table_rows[1].values[5] == "20.00"
```

- [ ] **Step 2: Run the touched tests to verify the new/updated ones fail**

Run: `./venv/bin/python -m pytest tests/test_layout_replica_parser.py -v`
Expected: `test_table_replica_keeps_rows_before_repeated_header_on_same_pdf_page`, `test_table_replica_infers_columns_when_header_is_missing`, `test_continuation_merges_into_description_and_reference_columns` FAIL (continuations still emitted as rows); `test_date_starting_line_is_never_merged_as_continuation` PASSES (guard regression test).

- [ ] **Step 3: Implement the merge in `_table_rows_from_page` + helpers**

In `parsers/layout_replica_parser.py`, replace the loop body of `_table_rows_from_page` (currently lines 605–630) with:

```python
        for line in page.lines:
            if self._is_separator_line(line):
                continue
            if header_line and line.index == header_line.index:
                continue

            values = self._assign_line_to_table_columns(line, columns)
            populated = sum(1 for value in values if value)
            if not populated:
                continue

            if self._is_continuation_row(line, values, columns, rows):
                self._merge_continuation_row(rows[-1], values)
                continue

            if not self._is_table_relevant_row(line, values, columns, saw_data_row):
                continue

            if populated == 1 and not saw_data_row:
                continue
            if populated >= 2:
                saw_data_row = True

            rows.append(TableReplicaRow(
                page=page.page_number,
                line=line.index,
                values=values,
                source=page.source,
            ))
        return rows
```

Then add the three helpers after `_join_words` (end of class, ~line 919):

```python
    def _is_continuation_row(
        self,
        line: LayoutLine,
        values: List[str],
        columns: List[TableColumn],
        page_rows: List[TableReplicaRow],
    ) -> bool:
        """A wrapped fragment of the previous row: no date, no amounts, and
        every populated cell sits in a text-ish column."""
        if not page_rows:
            return False
        if self._is_document_context_line(line.text.strip()):
            return False
        populated = [idx for idx, value in enumerate(values) if value]
        if not populated:
            return False
        if any(self._looks_like_date_value(value) for value in values[:2] if value):
            return False
        for idx in populated:
            if self._is_amount_header(columns[idx].header) and self._contains_amount_value(values[idx]):
                return False
            if not self._is_text_column(columns[idx], idx):
                return False
        return True

    def _is_text_column(self, column: TableColumn, idx: int) -> bool:
        header = column.header.lower()
        if self._is_wide_text_header(column.header):
            return True
        if any(token in header for token in ("reference", "ref", "remarks")):
            return True
        if header.startswith("column"):
            # positional layouts: description/reference usually sit at index 1-2
            return idx in {1, 2}
        return False

    @staticmethod
    def _merge_continuation_row(parent: TableReplicaRow, values: List[str]) -> None:
        for idx, value in enumerate(values):
            if not value or idx >= len(parent.values):
                continue
            parent.values[idx] = f"{parent.values[idx]} {value}".strip()
```

- [ ] **Step 4: Run the parser test file, then the full suite**

Run: `./venv/bin/python -m pytest tests/test_layout_replica_parser.py -v`
Expected: all PASS.
Run: `./venv/bin/python -m pytest tests/ -v`
Expected: all PASS (routes/email/parsers untouched by this change; investigate any failure before proceeding).

- [ ] **Step 5: Commit**

```bash
git add parsers/layout_replica_parser.py tests/test_layout_replica_parser.py
git commit -m "Merge wrapped continuation lines into one row per PDF transaction"
```

---

### Task 2: `Full_Text` sheet — nothing silently deleted

**Files:**
- Modify: `parsers/layout_replica_parser.py` (`write_excel` ~line 187; `_write_lines_sheet` ~line 983)
- Test: `tests/test_layout_replica_parser.py`

**Interfaces:**
- Consumes: `_write_lines_sheet(wb)` (exists, currently dead code).
- Produces: workbooks with sheets `["sheet1", "Full_Text"]`; `Full_Text` columns: Page | Line | Source | Text, one row per visual line, no filtering.

- [ ] **Step 1: Update sheet-list assertions and add the no-loss test**

In `tests/test_layout_replica_parser.py`:

(a) In `test_layout_replica_parser_preserves_visible_lines_and_strings`, change:

```python
    assert wb.sheetnames == ["sheet1"]
```
to:
```python
    assert wb.sheetnames == ["sheet1", "Full_Text"]
```

(b) Rename `test_layout_replica_workbook_exports_only_table_sheet` to `test_layout_replica_workbook_exports_table_and_full_text_sheets` and replace its body after `parser.write_excel(...)`:

```python
    wb = load_workbook(output_path)
    assert wb.sheetnames == ["sheet1", "Full_Text"]
    sheet = wb["sheet1"]
    assert sheet.max_column == 5
    assert sheet.max_row == 3

    full_text = wb["Full_Text"]
    assert [full_text.cell(1, col).value for col in range(1, 5)] == [
        "Page", "Line", "Source", "Text",
    ]
    total_lines = sum(len(page.lines) for page in parser.pages)
    assert full_text.max_row == total_lines + 1  # header + every visual line

    texts = [full_text.cell(row, 4).value for row in range(2, full_text.max_row + 1)]
    # content the table sheet drops must still be in the workbook
    assert any("Example Bank" in (t or "") for t in texts)
    assert any("Statement Details" in (t or "") for t in texts)
    # table content appears too (reading order, untouched)
    assert any("CARD PAYMENT ABC-123" in (t or "") for t in texts)
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_layout_replica_parser.py -v`
Expected: both updated tests FAIL with `['sheet1'] != ['sheet1', 'Full_Text']`.

- [ ] **Step 3: Write the sheet**

In `parsers/layout_replica_parser.py`, change `write_excel`:

```python
    def write_excel(self, output_path: str) -> None:
        """Write the table replica plus a Full_Text sheet with every visual line."""
        wb = Workbook()
        default_sheet = wb.active
        wb.remove(default_sheet)

        self._write_table_replica_sheet(wb)
        self._write_lines_sheet(wb)
        wb.save(output_path)
```

And in `_write_lines_sheet`, change the sheet title line:

```python
        sheet = wb.create_sheet("Full_Text")
```

- [ ] **Step 4: Run the parser test file, then the full suite**

Run: `./venv/bin/python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add parsers/layout_replica_parser.py tests/test_layout_replica_parser.py
git commit -m "Write Full_Text sheet so no PDF content is silently dropped"
```

---

### Task 3: Marketing honesty fixes (copy only)

**Files:**
- Modify: `templates/home.html:126,310`, `templates/pricing.html:227`, `templates/dashboard.html:264`

**Interfaces:** none (copy-only).

- [ ] **Step 1: Fix the free-tier promise (guests get 1, free account gets 5/month per app.py:64,85)**

`templates/home.html` line 126 — replace:

```html
                        <span>5 free conversions</span>
```
with:
```html
                        <span>Free conversion — no signup</span>
```

`templates/home.html` line 310 — replace:

```html
                <p>Start with 5 free conversions per month. No credit card needed.</p>
```
with:
```html
                <p>Try one conversion free — no signup. Free accounts get 5 conversions per month. No credit card needed.</p>
```

(`templates/signin.html:26` already says "Your free account includes 5 conversions per month" — accurate, leave as is.)

- [ ] **Step 2: Remove the nonexistent-API claim**

`templates/pricing.html` line 227 — delete the whole line:

```html
                    <li><svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7.5l3 3 5-5.5"/></svg> API access</li>
```

- [ ] **Step 3: Drop the third-party splitter link**

`templates/dashboard.html` line 264 — delete:

```html
            <a class="btn-primary" href="https://smallpdfsplit.online/" target="_blank" rel="noopener">Split your PDF</a>
```

(The modal keeps its message and Close button; `showLimitModal` in script.js already says "Split the PDF and try again.")

- [ ] **Step 4: Verify no stale copy remains and routes still render**

Run: `grep -rn "5 free\|smallpdfsplit\|API access" templates/`
Expected: no output.
Run: `./venv/bin/python -m pytest tests/test_routes.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/home.html templates/pricing.html templates/dashboard.html
git commit -m "Fix marketing copy to match actual product behavior"
```

---

### Task 4: "Retry in high quality" keeps the selected file

**Files:**
- Modify: `static/script.js` (declaration near line 14 block; `handleFileSelect` ~line 160; `retryWithHighQuality` ~line 638)

**Interfaces:**
- Produces: module-level `let lastSelectedFile = null;` set on every valid selection; retry restores it into the input via `DataTransfer` when the form was reset.

- [ ] **Step 1: Keep a reference to the last valid file**

After the `const fileInput = document.getElementById('pdf_file');` declaration (line 14), add:

```javascript
let lastSelectedFile = null;
```

In `handleFileSelect`, inside the `if (validateFile(file))` branch, add one line before `displayFileInfo(file);`:

```javascript
            lastSelectedFile = file;
```

- [ ] **Step 2: Restore the file on retry**

In `retryWithHighQuality`, replace:

```javascript
    if (fileInput.files.length > 0) {
        showProgressModal();
        submitFormWithProgress();
    } else {
        showAlert('Please re-select your PDF file, then click Convert.', 'warning');
    }
```
with:
```javascript
    if (fileInput.files.length === 0 && lastSelectedFile) {
        const dt = new DataTransfer();
        dt.items.add(lastSelectedFile);
        fileInput.files = dt.files;
        displayFileInfo(lastSelectedFile);
    }

    if (fileInput.files.length > 0) {
        showProgressModal();
        submitFormWithProgress();
    } else {
        showAlert('Please re-select your PDF file, then click Convert.', 'warning');
    }
```

- [ ] **Step 3: Syntax check + manual note**

Run: `node --check static/script.js`
Expected: no output (exit 0). (No JS test harness in repo; behavior verified in Task 5's end-to-end pass if a browser session is available, otherwise by code review.)

- [ ] **Step 4: Commit**

```bash
git add static/script.js
git commit -m "Keep selected file so high-quality retry works after form reset"
```

---

### Task 5: End-to-end verification on real scanned statements

**Files:** none modified (verification only; scratch script outside the repo).

- [ ] **Step 1: Run the replica parser on both KVB PDFs (OCR path, slow — minutes each)**

Write to the session scratchpad (NOT the repo) `verify_kvb.py`:

```python
import sys
sys.path.insert(0, "/Users/sasikumarad/Documents/Personal/PDF-XLS-Converter")
import re
from openpyxl import load_workbook
from parsers.layout_replica_parser import LayoutReplicaParser

PDFS = [
    "/Users/sasikumarad/Documents/Personal/PDF-XLS-Converter/KVB-CA-PART-02- 01.07.2018 TO 31.03.2019.pdf",
    "/Users/sasikumarad/Documents/Personal/PDF-XLS-Converter/KVB-CA-PART-03- 01.04.2019 TO 31.03.2020.pdf",
]
for pdf in PDFS:
    out = pdf.rsplit("/", 1)[-1][:20].replace(" ", "_") + "_v2.xlsx"
    p = LayoutReplicaParser(use_ocr=True)
    p.parse(pdf, pdf.rsplit("/", 1)[-1])
    p.write_excel(out)
    wb = load_workbook(out)
    table, full = wb["sheet1"], wb["Full_Text"]
    rows = list(table.iter_rows(values_only=True))
    dated = sum(1 for r in rows[1:] if r[0] and re.match(r"\d{2}/\d{2}/\d{2}", str(r[0])))
    orphans = sum(1 for r in rows[1:] if not r[0] and any(r))
    print(f"{out}: table={len(rows)-1} rows ({dated} dated, {orphans} orphan), "
          f"full_text={full.max_row-1} lines, cols={rows[0]}")
```

Run: `./venv/bin/python <scratchpad>/verify_kvb.py` (from the scratchpad dir)

- [ ] **Step 2: Check the acceptance signals**

Expected:
- PART-03: table rows ≈ dated rows (~2,148; previously 4,491 with 2,343 orphan continuation rows). Orphan count near zero.
- Both files: `full_text` line count > table row count (headers/footers/account info present).
- Header row still the bank's own columns (TXN DT | VALUE_DT | BRN | DESCRIPTION | REFERENCE | DEBITS | CREDITS | BALANCE).
- Spot-check in the PART-03 output that a known wrap ("ATM CSW/0100162693/Kovai-" + "Salem Rd/Salem") is one row.
- `Full_Text` contains an account-header line (e.g. "Account Number") absent from the table sheet.

- [ ] **Step 3: Full suite once more, then merge**

Run: `./venv/bin/python -m pytest tests/ -v`
Expected: all PASS.

```bash
git checkout main && git merge --no-ff exact-copy-fidelity -m "Exact-copy fidelity: merged rows, Full_Text sheet, honest marketing copy"
```
(Merge to main only after the user confirms, or immediately if the user already authorized — they approved "go ahead with everything".)
