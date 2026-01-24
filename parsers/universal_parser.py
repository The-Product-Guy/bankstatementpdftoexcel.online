#!/usr/bin/env python3
"""
Universal Bank Statement Parser
Works with any bank statement format globally by combining:
1. PaddleOCR for enhanced text extraction
2. OpenAI Vision for intelligent table understanding
3. Fallback to legacy parsers for known formats
"""
import os
import re
import gc
from typing import List, Dict, Any, Optional, Tuple, Callable
from dataclasses import dataclass
from PIL import Image
from datetime import datetime

from .base_parser import BaseParser


@dataclass
class ProcessingConfig:
    """Configuration for document processing."""
    use_paddleocr: bool = True
    use_llm: bool = True
    use_table_structure: bool = True
    prefer_vision: bool = False  # If True, send images directly to LLM
    llm_model: str = "gpt-4o-mini"
    max_pages: Optional[int] = None  # Limit for SaaS
    dpi: int = 200
    preprocess_images: bool = True
    min_table_transactions: Optional[int] = 5


@dataclass 
class ProcessingStats:
    """Statistics about the processing job."""
    total_pages: int
    pages_processed: int
    transactions_found: int
    ocr_method: str
    llm_tokens_used: int
    estimated_cost: float
    processing_time_seconds: float


class UniversalBankParser(BaseParser):
    """
    Universal parser that works with any bank statement format.
    Combines enhanced OCR with LLM-based intelligent extraction.
    """
    
    def __init__(
        self, 
        progress_callback: Optional[Callable] = None,
        config: Optional[ProcessingConfig] = None
    ):
        super().__init__(progress_callback)
        self.bank_name = "Universal"
        self.config = config or ProcessingConfig()
        self.stats = None
        self.raw_table = None
        
        # Lazy-loaded processors
        self._paddle_processor = None
        self._llm_extractor = None
        self._tesseract_available = True
    
    @property
    def paddle_processor(self):
        """Lazy load PaddleOCR processor."""
        if self._paddle_processor is None and self.config.use_paddleocr:
            try:
                from .paddleocr_processor import PaddleOCRProcessor
                self._paddle_processor = PaddleOCRProcessor(
                    use_table_structure=self.config.use_table_structure
                )
            except ImportError:
                print("  ⚠️ PaddleOCR not available, falling back to Tesseract")
        return self._paddle_processor
    
    @property
    def llm_extractor(self):
        """Lazy load LLM extractor."""
        if self._llm_extractor is None and self.config.use_llm:
            try:
                from .llm_table_extractor import LLMTableExtractor
                self._llm_extractor = LLMTableExtractor(model=self.config.llm_model)
            except Exception as e:
                print(f"  ⚠️ LLM extractor not available: {e}")
        return self._llm_extractor
    
    def parse(self, pdf_path: str, original_filename: str) -> List[Dict[str, Any]]:
        """
        Parse any bank statement PDF.
        
        Args:
            pdf_path: Path to the PDF file
            original_filename: Original name of the uploaded file
            
        Returns:
            List of transaction dictionaries
        """
        import time
        start_time = time.time()
        
        self.validate_pdf_file(pdf_path)
        self.transactions = []
        
        print(f"  🌐 Universal Parser processing: {original_filename}")
        
        # Detect PDF type
        pdf_type = self.detect_pdf_type(pdf_path)
        print(f"  📄 Detected {pdf_type}-based PDF")
        
        # Get page count and validate against limits
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
        
        if self.config.max_pages and total_pages > self.config.max_pages:
            raise ValueError(
                f"PDF has {total_pages} pages but limit is {self.config.max_pages}. "
                f"Please upgrade your plan or split the document."
            )
        
        print(f"  📑 Processing {total_pages} pages")
        
        total_tokens = 0
        ocr_method = "paddleocr" if self.paddle_processor else "tesseract"
        
        if pdf_type == "text":
            # Text-based PDF - extract directly and use LLM if available
            total_tokens = self._process_text_based(pdf_path, original_filename)
        else:
            # Image-based PDF - use enhanced OCR pipeline
            total_tokens = self._process_image_based(
                pdf_path, original_filename, total_pages
            )
        
        # Calculate stats
        processing_time = time.time() - start_time
        estimated_cost = 0.0
        
        if self.llm_extractor and total_tokens > 0:
            estimated_cost = self.llm_extractor.estimate_cost(
                num_pages=total_pages,
                text_length=total_tokens * 4  # Rough estimate
            )
        
        self.stats = ProcessingStats(
            total_pages=total_pages,
            pages_processed=total_pages,
            transactions_found=len(self.transactions),
            ocr_method=ocr_method,
            llm_tokens_used=total_tokens,
            estimated_cost=estimated_cost,
            processing_time_seconds=processing_time
        )
        
        print(f"  🎉 Extracted {len(self.transactions)} transactions in {processing_time:.1f}s")
        
        return self.transactions
    
    def _process_text_based(
        self, 
        pdf_path: str, 
        source_file: str
    ) -> int:
        """
        Process text-based PDF using direct extraction. 
        Uses page-based chunking (5 pages per chunk) for reliable LLM extraction.
        Returns tokens used.
        """
        import pdfplumber
        
        chunk_mode = os.environ.get("LLM_TEXT_CHUNKING_MODE", "pages").strip().lower()
        pages_per_chunk = self._get_int_env("LLM_TEXT_PAGES_PER_CHUNK", 10)
        target_chars = self._get_int_env("LLM_TEXT_TARGET_CHARS", 12000)
        max_chars = self._get_int_env("LLM_TEXT_MAX_CHARS", 18000)
        
        # Extract all pages' text and try table extraction first
        page_texts = []
        table_transactions = []
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            
            for page_num, page in enumerate(pdf.pages, 1):
                self.emit_progress(
                    page_num, total_pages, 
                    f"Extracting text from page {page_num}/{total_pages}",
                    stage="text_extract"
                )
                
                page_text = page.extract_text()
                if page_text:
                    page_texts.append((page_num, page_text))

                try:
                    tables = page.extract_tables(self._table_settings())
                except Exception:
                    tables = []

                for table in tables:
                    table_transactions.extend(
                        self._transactions_from_table(
                            table,
                            source_file=source_file,
                            page_ref=f"Page_{page_num}"
                        )
                    )
        
        total_chars = sum(len(text) for _, text in page_texts)
        print(f"  📄 Extracted {total_chars:,} characters from {len(page_texts)} pages")

        if table_transactions:
            min_table_tx = self.config.min_table_transactions
            if min_table_tx is None or len(table_transactions) >= min_table_tx:
                if self._transactions_quality_ok(table_transactions):
                    print(f"  📊 Table extraction found {len(table_transactions)} transactions; skipping LLM.")
                    self.transactions.extend(table_transactions)
                    return 0
                print("  ⚠️ Table extraction quality low, falling back to layout/LLM.")

        layout_transactions, raw_table = self._extract_layout_transactions(pdf_path, source_file)
        if layout_transactions and self._transactions_quality_ok(layout_transactions):
            print(f"  📐 Layout extraction found {len(layout_transactions)} transactions; skipping LLM.")
            self.transactions.extend(layout_transactions)
            self.raw_table = raw_table
            return 0
        
        if not self.llm_extractor:
            # Fallback to regex-based parsing
            all_text = "\n\n".join(text for _, text in page_texts)
            self._fallback_regex_parse(all_text, source_file)
            return 0
        
        # Detect columns from first page
        if page_texts:
            first_page_text = page_texts[0][1]
            print("  🔍 Detecting column structure from first page...")
            column_mapping = self.llm_extractor.detect_columns(first_page_text)
            print(f"  📊 Detected columns: {', '.join(column_mapping.columns)}")
        
        # Build chunks list
        chunks = self._build_chunks(
            page_texts=page_texts,
            chunk_mode=chunk_mode,
            pages_per_chunk=pages_per_chunk,
            target_chars=target_chars,
            max_chars=max_chars
        )
        num_chunks = len(chunks)
        
        print(f"  📦 Processing {num_chunks} chunks in parallel ({chunk_mode} chunking)...")
        self.emit_progress(
            0, num_chunks,
            "LLM starting...",
            stage="llm_text"
        )
        
        # Process chunks in parallel (5 concurrent requests)
        MAX_WORKERS = min(self._get_int_env("LLM_MAX_WORKERS", 5), max(1, num_chunks))
        total_tokens = 0
        results_by_idx = {}
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def process_chunk(chunk_data):
            chunk_idx, page_range, chunk_text = chunk_data
            result = self.llm_extractor.extract_from_text(chunk_text, column_mapping)
            return chunk_idx, page_range, result
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_chunk, chunk): chunk[0] for chunk in chunks}
            completed = 0
            
            for future in as_completed(futures):
                completed += 1
                try:
                    chunk_idx, page_range, result = future.result()
                    results_by_idx[chunk_idx] = (page_range, result)
                    
                    if result.success:
                        print(f"    ✅ Chunk {chunk_idx + 1}/{num_chunks}: {len(result.transactions)} transactions ({result.tokens_used} tokens)")
                    else:
                        print(f"    ⚠️ Chunk {chunk_idx + 1}/{num_chunks} failed: {result.error_message}")
                    
                    self.emit_progress(
                        completed, num_chunks,
                        f"LLM extracting chunk {completed}/{num_chunks}",
                        stage="llm_text"
                    )
                except Exception as e:
                    print(f"    ❌ Chunk error: {e}")
        
        # Collect results in order
        for chunk_idx in range(num_chunks):
            if chunk_idx in results_by_idx:
                page_range, result = results_by_idx[chunk_idx]
                if result.success:
                    for tx in result.transactions:
                        tx['Source_File'] = source_file
                        tx['Page_Line'] = f"Pages_{page_range}"
                        self.transactions.append(tx)
                    total_tokens += result.tokens_used
        
        return total_tokens

    def _extract_layout_transactions(self, pdf_path: str, source_file: str) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Extract transactions using word coordinates and header positions."""
        import pdfplumber

        transactions: List[Dict[str, Any]] = []
        last_tx: Optional[Dict[str, Any]] = None
        raw_columns: Optional[List[str]] = None
        raw_rows: List[List[str]] = []
        raw_col_map: Optional[Dict[str, int]] = None

        last_header = None
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                header = self._detect_header_layout(page)
                if header:
                    last_header = header
                elif last_header:
                    header = dict(last_header)
                    header["header_top"] = 0
                else:
                    continue
                if raw_columns is None:
                    raw_columns = self._normalize_header_labels([g["text"] for g in header["columns"]])
                    raw_col_map = header["col_map"]

                rows = self._extract_rows_from_page(page, header)
                for row in rows:
                    if self._is_separator_row(row):
                        continue
                    if raw_columns and self._is_header_row(row, raw_columns):
                        continue
                    raw_rows.append(row)

                    tx = self._row_to_transaction(
                        row=row,
                        col_map=header["col_map"],
                        source_file=source_file,
                        page_ref=f"Page_{page_num}"
                    )
                    if tx:
                        transactions.append(tx)
                        last_tx = tx
                    else:
                        # Continuation line (reference/details)
                        if last_tx and any(cell.strip() for cell in row):
                            ref_idx = header["col_map"].get("reference")
                            desc_idx = header["col_map"].get("description")
                            ref_text = row[ref_idx].strip() if ref_idx is not None and ref_idx < len(row) else ""
                            desc_text = row[desc_idx].strip() if desc_idx is not None and desc_idx < len(row) else ""
                            extra = ref_text or desc_text or " ".join(c for c in row if c.strip())
                            if extra:
                                if self._looks_like_reference(extra):
                                    last_tx["Reference_Number"] = (last_tx.get("Reference_Number") or "") + extra
                                else:
                                    last_tx["Description"] = (last_tx.get("Description") or "") + " " + extra
        raw_table = None
        if raw_columns and raw_rows and raw_col_map:
            raw_rows = self._merge_continuation_rows(raw_rows, raw_col_map)
            raw_table = {"columns": raw_columns, "rows": raw_rows}

        return transactions, raw_table

    def _normalize_header_labels(self, headers: List[str]) -> List[str]:
        """Normalize header labels and de-duplicate."""
        normalized = []
        seen = {}
        for header in headers:
            label = " ".join(header.strip().split())
            if not label:
                label = "Column"
            if label in seen:
                seen[label] += 1
                label = f"{label}_{seen[label]}"
            else:
                seen[label] = 1
            normalized.append(label)
        return normalized

    def _is_separator_row(self, row: List[str]) -> bool:
        return all(cell and set(cell) <= {"-"} for cell in row if cell.strip()) and any(cell.strip() for cell in row)

    def _is_header_row(self, row: List[str], headers: List[str]) -> bool:
        joined = " ".join(cell.lower() for cell in row if cell).strip()
        if not joined:
            return False
        header_text = " ".join(h.lower() for h in headers)
        if all(token in joined for token in header_text.split()[:3]):
            return True
        if self._row_has_header_tokens(joined) and not self._looks_like_date(joined):
            return True
        return False

    def _merge_continuation_rows(
        self,
        rows: List[List[str]],
        col_map: Dict[str, int]
    ) -> List[List[str]]:
        merged: List[List[str]] = []
        last = None
        date_idx = col_map.get("date", 0)
        desc_idx = col_map.get("description")
        ref_idx = col_map.get("reference")

        for row in rows:
            date_val = row[date_idx].strip() if date_idx < len(row) else ""
            if date_val and self._looks_like_date(date_val):
                merged.append(row)
                last = row
                continue

            if last is None:
                merged.append(row)
                continue

            if ref_idx is not None and ref_idx < len(row):
                extra_ref = row[ref_idx].strip()
                if extra_ref:
                    last[ref_idx] = f"{last[ref_idx]} {extra_ref}".strip()
            if desc_idx is not None and desc_idx < len(row):
                extra_desc = row[desc_idx].strip()
                if extra_desc and not self._looks_like_reference(extra_desc):
                    last[desc_idx] = f"{last[desc_idx]} {extra_desc}".strip()

        return merged

    def _detect_header_layout(self, page) -> Optional[Dict[str, Any]]:
        """Detect header row and column boundaries using word positions."""
        words = page.extract_words()
        if not words:
            return None

        header_tokens = {
            "txn", "dt", "date", "value", "brn", "description",
            "reference", "debits", "credits", "balance",
            "s", "no", "transaction", "cheque", "number", "remarks",
            "withdrawal", "deposit", "amount", "inr"
        }

        def normalize(text: str) -> str:
            cleaned = text.lower().replace("_", " ")
            cleaned = re.sub(r"[^a-z0-9\\s]", " ", cleaned)
            cleaned = re.sub(r"\\s+", " ", cleaned).strip()
            return cleaned

        def token_list(text: str) -> List[str]:
            return normalize(text).split()

        # Group potential header words by y position
        y_groups: Dict[float, List[Dict[str, Any]]] = {}
        for w in words:
            tokens = token_list(w["text"])
            if any(t in header_tokens for t in tokens):
                key = round(w["top"], 1)
                y_groups.setdefault(key, []).append(w)

        if not y_groups:
            return None

        header_top, header_words = max(y_groups.items(), key=lambda item: len(item[1]))
        if len(header_words) < 4:
            return None

        # Merge header tokens into column labels using known pairs
        header_words = sorted(header_words, key=lambda w: w["x0"])
        header_groups: List[Dict[str, Any]] = []
        merge_threshold = 25
        merge_pairs = {
            ("txn", "dt"),
            ("value", "dt"),
            ("value", "date"),
            ("s", "no"),
            ("transaction", "date"),
            ("cheque", "number"),
            ("transaction", "remarks"),
            ("withdrawal", "amount"),
            ("deposit", "amount"),
            ("balance", "inr"),
            ("closing", "balance"),
            ("opening", "balance"),
        }

        def norm_token(text: str) -> str:
            return normalize(text)

        for w in header_words:
            token = norm_token(w["text"])
            token_first = token.split()[0] if token else ""
            if not header_groups:
                header_groups.append({
                    "x0": w["x0"],
                    "x1": w["x1"],
                    "text": w["text"],
                    "token": token
                })
                continue

            prev = header_groups[-1]
            prev_token = prev["token"].split()[-1] if prev["token"] else ""
            pair = (prev_token, token_first)

            if pair in merge_pairs and w["x0"] - prev["x1"] <= merge_threshold:
                prev["x1"] = max(prev["x1"], w["x1"])
                prev["text"] = f"{prev['text']} {w['text']}"
                prev["token"] = f"{prev['token']} {token}".strip()
            else:
                header_groups.append({
                    "x0": w["x0"],
                    "x1": w["x1"],
                    "text": w["text"],
                    "token": token
                })

        # Merge second-line header words like "Amount(INR)"
        secondary_words = [
            w for w in words
            if 0 < (w["top"] - header_top) <= 14
            and any(t in header_tokens for t in token_list(w["text"]))
        ]
        for w in secondary_words:
            token = norm_token(w["text"])
            if not token:
                continue
            # Append to nearest header group
            target = min(header_groups, key=lambda g: abs(w["x0"] - g["x0"]))
            target["text"] = f"{target['text']} {w['text']}".strip()
            target["token"] = f"{target['token']} {token}".strip()

        # Build boundaries
        xs = [g["x0"] for g in header_groups]
        if len(xs) < 2:
            return None

        boundaries = [0.0]
        for i in range(1, len(xs)):
            boundaries.append((xs[i - 1] + xs[i]) / 2)
        boundaries.append(page.width + 1)

        col_map = self._map_headers([g["text"] for g in header_groups])
        if "date" not in col_map or "balance" not in col_map:
            return None

        return {
            "columns": header_groups,
            "boundaries": boundaries,
            "col_map": col_map,
            "header_top": header_top
        }

    def _row_has_header_tokens(self, text: str) -> bool:
        import re
        cleaned = text.lower().replace("_", " ")
        cleaned = re.sub(r"[^a-z0-9\\s]", " ", cleaned)
        cleaned = re.sub(r"\\s+", " ", cleaned).strip()
        tokens = cleaned.split()
        header_tokens = {
            "s", "no", "value", "date", "transaction", "cheque", "number",
            "remarks", "withdrawal", "deposit", "balance", "amount", "inr"
        }
        return sum(1 for t in tokens if t in header_tokens) >= 3

    def _extract_rows_from_page(self, page, header: Dict[str, Any]) -> List[List[str]]:
        """Extract rows below header using column boundaries."""
        words = page.extract_words()
        if not words:
            return []

        # Group words into rows by y position
        row_tolerance = 3
        rows: List[List[Dict[str, Any]]] = []
        current_row: List[Dict[str, Any]] = []
        current_top = None

        for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
            if w["top"] <= header["header_top"] + 2:
                continue
            if current_top is None or abs(w["top"] - current_top) <= row_tolerance:
                current_row.append(w)
                current_top = w["top"] if current_top is None else current_top
            else:
                rows.append(current_row)
                current_row = [w]
                current_top = w["top"]

        if current_row:
            rows.append(current_row)

        boundaries = header["boundaries"]
        table_rows: List[List[str]] = []

        for row_words in rows:
            cols = [""] * (len(boundaries) - 1)
            for w in row_words:
                idx = self._boundary_index(w["x0"], boundaries)
                if idx is None:
                    continue
                cols[idx] = f"{cols[idx]} {w['text']}".strip()
            table_rows.append(cols)

        return table_rows

    def _boundary_index(self, x0: float, boundaries: List[float]) -> Optional[int]:
        for i in range(len(boundaries) - 1):
            if boundaries[i] <= x0 < boundaries[i + 1]:
                return i
        return None

    def _looks_like_reference(self, text: str) -> bool:
        digits = sum(1 for c in text if c.isdigit())
        letters = sum(1 for c in text if c.isalpha())
        return digits >= 6 and digits >= letters

    def _transactions_quality_ok(self, transactions: List[Dict[str, Any]]) -> bool:
        """Heuristic quality check to decide if extraction is usable."""
        if not transactions:
            return False

        total = len(transactions)
        date_like_desc = 0
        missing_amount = 0
        suspicious_amount = 0

        for tx in transactions:
            desc = (tx.get("Description") or "").strip()
            if desc and self._looks_like_date(desc):
                date_like_desc += 1

            if not tx.get("Withdrawal_Amount") and not tx.get("Deposit_Amount") and not tx.get("Transaction_Amount"):
                missing_amount += 1

            balance = tx.get("Closing_Balance")
            amount = tx.get("Transaction_Amount")
            if balance and amount and amount > 1e7 and balance < (amount / 5):
                suspicious_amount += 1

        if date_like_desc / total > 0.3:
            return False
        if missing_amount / total > 0.3:
            return False
        if suspicious_amount / total > 0.2:
            return False

        return True

    def _looks_like_date(self, text: str) -> bool:
        import re
        return bool(re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', text))

    def _table_settings(self) -> Dict[str, Any]:
        """Table extraction settings for pdfplumber."""
        return {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "intersection_tolerance": 5,
            "snap_tolerance": 2,
            "join_tolerance": 2,
            "min_words_vertical": 3,
            "min_words_horizontal": 1,
        }

    def _get_int_env(self, name: str, default: int) -> int:
        """Read integer from environment with fallback."""
        try:
            return int(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default

    def _build_chunks(
        self,
        page_texts: List[Tuple[int, str]],
        chunk_mode: str,
        pages_per_chunk: int,
        target_chars: int,
        max_chars: int
    ) -> List[Tuple[int, str, str]]:
        """Build LLM chunks from page text."""
        if not page_texts:
            return []

        if max_chars < target_chars:
            max_chars = target_chars

        chunks: List[Tuple[int, str, str]] = []

        def finalize(chunk_idx: int, pages: List[Tuple[int, str]]):
            page_range = f"{pages[0][0]}-{pages[-1][0]}"
            chunk_text = "\n\n".join(
                f"--- Page {page_num} ---\n{text}"
                for page_num, text in pages
            )
            chunks.append((chunk_idx, page_range, chunk_text))

        if chunk_mode == "pages":
            total = len(page_texts)
            num_chunks = (total + pages_per_chunk - 1) // pages_per_chunk
            for chunk_idx in range(num_chunks):
                start_idx = chunk_idx * pages_per_chunk
                end_idx = min(start_idx + pages_per_chunk, total)
                finalize(chunk_idx, page_texts[start_idx:end_idx])
            return chunks

        chunk_idx = 0
        current_pages: List[Tuple[int, str]] = []
        current_chars = 0

        for page_num, text in page_texts:
            text_len = len(text)
            if current_pages and current_chars + text_len > max_chars:
                finalize(chunk_idx, current_pages)
                chunk_idx += 1
                current_pages = []
                current_chars = 0

            current_pages.append((page_num, text))
            current_chars += text_len

            if current_chars >= target_chars:
                finalize(chunk_idx, current_pages)
                chunk_idx += 1
                current_pages = []
                current_chars = 0

        if current_pages:
            finalize(chunk_idx, current_pages)

        return chunks

    def _transactions_from_table(
        self,
        table: List[List[Any]],
        source_file: str,
        page_ref: str
    ) -> List[Dict[str, Any]]:
        """Convert a table (list of rows) to standardized transactions."""
        if not table:
            return []

        # Clean rows and normalize column counts
        rows = [
            [str(cell).strip() if cell is not None else "" for cell in row]
            for row in table
            if row
        ]
        if not rows:
            return []

        max_cols = max(len(r) for r in rows)
        rows = [r + [""] * (max_cols - len(r)) for r in rows]

        header_idx = self._find_header_row(rows)
        if header_idx is not None:
            headers = rows[header_idx]
            data_rows = rows[header_idx + 1:]
            col_map = self._map_headers(headers)
        else:
            headers = []
            data_rows = rows
            col_map = self._infer_columns_from_rows(rows)

        transactions = []
        for row in data_rows:
            if not any(cell.strip() for cell in row):
                continue

            tx = self._row_to_transaction(
                row=row,
                col_map=col_map,
                source_file=source_file,
                page_ref=page_ref
            )
            if tx:
                transactions.append(tx)

        return transactions

    def _find_header_row(self, rows: List[List[str]]) -> Optional[int]:
        """Find a probable header row by keyword matching."""
        header_keywords = [
            "date", "txn", "value", "description", "narration", "particular",
            "reference", "ref", "cheque", "debit", "credit", "withdraw", "deposit",
            "balance", "amount"
        ]

        for idx, row in enumerate(rows[:3]):
            joined = " ".join(cell.lower() for cell in row if cell)
            if any(k in joined for k in header_keywords):
                return idx
        return None

    def _map_headers(self, headers: List[str]) -> Dict[str, int]:
        """Map header names to standard fields."""
        mapping: Dict[str, int] = {}
        for idx, header in enumerate(headers):
            h = header.lower().strip()
            if not h:
                continue

            if 'date' in h or 'txn' in h or 'value' in h:
                mapping.setdefault('date', idx)
            elif any(x in h for x in ['desc', 'narr', 'partic', 'remark', 'details']):
                mapping.setdefault('description', idx)
            elif any(x in h for x in ['ref', 'cheq', 'chq', 'utr', 'instrument']):
                mapping.setdefault('reference', idx)
            elif any(x in h for x in ['debit', 'withdraw', 'dr']):
                mapping.setdefault('debit', idx)
            elif any(x in h for x in ['credit', 'deposit', 'cr']):
                mapping.setdefault('credit', idx)
            elif 'balance' in h or 'bal' in h:
                mapping.setdefault('balance', idx)
        return mapping

    def _infer_columns_from_rows(self, rows: List[List[str]]) -> Dict[str, int]:
        """Infer column roles when no header is available."""
        import re

        date_re = re.compile(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}')
        amount_re = re.compile(r'^[\d,]+\.?\d{0,2}$')

        sample_rows = rows[:5]
        col_count = max(len(r) for r in sample_rows)
        date_scores = [0] * col_count
        amount_scores = [0] * col_count

        for row in sample_rows:
            for idx, cell in enumerate(row):
                cell_clean = cell.replace(" ", "")
                if date_re.search(cell_clean):
                    date_scores[idx] += 1
                if amount_re.match(cell_clean):
                    amount_scores[idx] += 1

        mapping: Dict[str, int] = {}
        if max(date_scores) > 0:
            mapping['date'] = int(date_scores.index(max(date_scores)))

        # Use last numeric columns for amounts
        amount_cols = sorted(
            range(col_count),
            key=lambda i: amount_scores[i],
            reverse=True
        )
        amount_cols = [i for i in amount_cols if amount_scores[i] > 0]

        if amount_cols:
            mapping['balance'] = amount_cols[0]
            if len(amount_cols) > 1:
                mapping['credit'] = amount_cols[1]
            if len(amount_cols) > 2:
                mapping['debit'] = amount_cols[2]

        # Description: first non-date, non-amount column
        for idx in range(col_count):
            if idx == mapping.get('date'):
                continue
            if idx in [mapping.get('debit'), mapping.get('credit'), mapping.get('balance')]:
                continue
            mapping.setdefault('description', idx)
            break

        return mapping

    def _row_to_transaction(
        self,
        row: List[str],
        col_map: Dict[str, int],
        source_file: str,
        page_ref: str
    ) -> Optional[Dict[str, Any]]:
        """Convert a table row into a transaction dict."""
        import re

        def get_cell(key: str) -> str:
            idx = col_map.get(key)
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        date_val = get_cell('date')
        date_re = re.compile(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}')
        if not date_re.search(date_val):
            return None

        description = get_cell('description')
        reference = get_cell('reference')

        withdrawal = self.clean_amount_string(get_cell('debit'))
        deposit = self.clean_amount_string(get_cell('credit'))
        balance = self.clean_amount_string(get_cell('balance'))

        if withdrawal is None and deposit is None and balance is None:
            # Attempt fallback from any numeric cells
            amounts = [
                self.clean_amount_string(cell)
                for cell in row
                if cell and self.clean_amount_string(cell) is not None
            ]
            amounts = [a for a in amounts if a is not None]
            if len(amounts) >= 3:
                withdrawal, deposit, balance = amounts[0], amounts[1], amounts[-1]
            elif len(amounts) == 2:
                desc_lower = description.lower()
                if any(w in desc_lower for w in ['cr', 'credit', 'deposit']):
                    deposit = amounts[0]
                else:
                    withdrawal = amounts[0]
                balance = amounts[1]
            elif len(amounts) == 1:
                balance = amounts[0]

        return self.create_transaction_dict(
            date=date_val,
            description=description,
            reference=reference,
            withdrawal_amt=withdrawal,
            deposit_amt=deposit,
            balance_amt=balance,
            source_file=source_file,
            line_ref=page_ref
        )
    
    def _process_image_based(
        self, 
        pdf_path: str, 
        source_file: str,
        total_pages: int
    ) -> int:
        """
        Process image-based PDF with enhanced OCR pipeline.
        Returns total tokens used by LLM.
        """
        from pdf2image import convert_from_path
        
        total_tokens = 0
        page_images = []
        page_texts = []
        total_ocr_chars = 0
        table_transactions = []
        
        # Process each page
        for page_num in range(total_pages):
            self.emit_progress(
                page_num + 1, total_pages,
                f"Processing page {page_num + 1}/{total_pages}",
                stage="ocr"
            )
            
            print(f"    📝 Processing page {page_num + 1}...")
            
            # Convert PDF page to image
            images = convert_from_path(
                pdf_path, 
                dpi=self.config.dpi,
                first_page=page_num + 1,
                last_page=page_num + 1
            )
            
            if not images:
                continue
            
            page_image = images[0]
            table_image = page_image
            
            # Preprocess image if enabled
            if self.config.preprocess_images:
                from .image_preprocessor import preprocess_for_ocr
                page_image = preprocess_for_ocr(page_image)
            
            # Run OCR
            if self.paddle_processor:
                page_text = self.paddle_processor.process_image_to_text(page_image)
            else:
                # Fallback to Tesseract
                import pytesseract
                page_text = pytesseract.image_to_string(page_image)
            
            page_texts.append((page_num + 1, page_text))
            total_ocr_chars += len(page_text)

            if self.config.use_table_structure and self.paddle_processor:
                try:
                    tables = self.paddle_processor.detect_table_structure(table_image) or []
                except Exception:
                    tables = []

                for table in tables:
                    html = table.get('html', '')
                    if not html:
                        continue
                    rows = self._parse_table_html(html)
                    table_transactions.extend(
                        self._transactions_from_table(
                            rows,
                            source_file=source_file,
                            page_ref=f"Page_{page_num + 1}"
                        )
                    )
            
            # Store image for vision processing if needed
            if self.config.prefer_vision:
                page_images.append(page_image)
            else:
                del page_image
            
            # Clear memory
            del images
            if page_num % 3 == 0:
                gc.collect()
        
        print(f"  📄 OCR extracted {total_ocr_chars:,} characters")

        if table_transactions:
            min_table_tx = self.config.min_table_transactions
            table_transactions = self._dedupe_transactions(table_transactions)
            if min_table_tx is None or len(table_transactions) >= min_table_tx:
                print(f"  📊 Table structure found {len(table_transactions)} transactions; skipping LLM.")
                self.transactions.extend(table_transactions)
                return 0
        
        # Extract transactions using LLM
        if self.llm_extractor:
            if self.config.prefer_vision and page_images:
                # Process each page with vision
                self.emit_progress(
                    0, len(page_images),
                    "LLM starting...",
                    stage="llm_ocr"
                )
                for i, img in enumerate(page_images):
                    self.emit_progress(
                        i + 1, len(page_images),
                        f"LLM processing page {i + 1}/{len(page_images)}",
                        stage="llm_ocr"
                    )
                    result = self.llm_extractor.extract_from_image(img)
                    if result.success:
                        self.transactions.extend(result.transactions)
                        total_tokens += result.tokens_used
                        # Add source file info
                        for tx in self.transactions[-len(result.transactions):]:
                            tx['Source_File'] = source_file
                            tx['Page_Line'] = f"Page_{i+1}"
            else:
                # Use text-based extraction with page chunking
                if not page_texts:
                    return total_tokens

                first_page_text = next((text for _, text in page_texts if text.strip()), page_texts[0][1])
                column_mapping = self.llm_extractor.detect_columns(first_page_text)
                print(f"  📊 Detected columns: {', '.join(column_mapping.columns)}")

                chunk_mode = os.environ.get("LLM_OCR_CHUNKING_MODE", "adaptive").strip().lower()
                pages_per_chunk = self._get_int_env("LLM_OCR_PAGES_PER_CHUNK", 5)
                target_chars = self._get_int_env("LLM_OCR_TARGET_CHARS", 8000)
                max_chars = self._get_int_env("LLM_OCR_MAX_CHARS", 12000)
                chunks = self._build_chunks(
                    page_texts=page_texts,
                    chunk_mode=chunk_mode,
                    pages_per_chunk=pages_per_chunk,
                    target_chars=target_chars,
                    max_chars=max_chars
                )
                num_chunks = len(chunks)
                print(f"  📦 Processing {num_chunks} chunks ({chunk_mode} chunking)...")
                self.emit_progress(
                    0, num_chunks,
                    "LLM starting...",
                    stage="llm_ocr"
                )

                for chunk_idx, page_range, chunk_text in chunks:
                    result = self.llm_extractor.extract_from_text(chunk_text, column_mapping)
                    if result.success:
                        for tx in result.transactions:
                            tx['Source_File'] = source_file
                            tx['Page_Line'] = f"Pages_{page_range}"
                            self.transactions.append(tx)
                        total_tokens += result.tokens_used
                        print(f"    ✅ Chunk {chunk_idx + 1}/{num_chunks}: {len(result.transactions)} transactions")
                    else:
                        print(f"    ⚠️ Chunk {chunk_idx + 1}/{num_chunks} failed: {result.error_message}")
                    self.emit_progress(
                        chunk_idx + 1, num_chunks,
                        f"LLM extracting chunk {chunk_idx + 1}/{num_chunks}",
                        stage="llm_ocr"
                    )
        else:
            # Fallback parsing
            all_ocr_text = "\n\n".join(
                f"--- Page {page_num} ---\n{text}"
                for page_num, text in page_texts
            )
            self._fallback_regex_parse(all_ocr_text, source_file)
        
        # Clean up
        page_images.clear()
        gc.collect()
        
        return total_tokens

    def _parse_table_html(self, html: str) -> List[List[str]]:
        """Parse HTML table into list of rows."""
        import re

        rows = []
        if not html:
            return rows

        for tr in re.findall(r'<tr[^>]*>.*?</tr>', html, flags=re.IGNORECASE | re.DOTALL):
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, flags=re.IGNORECASE | re.DOTALL)
            cleaned = []
            for cell in cells:
                text = re.sub(r'<[^>]+>', ' ', cell)
                text = re.sub(r'&nbsp;|&#160;', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                cleaned.append(text)
            if cleaned:
                rows.append(cleaned)
        return rows

    def _dedupe_transactions(self, transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate transactions based on key fields."""
        seen = set()
        unique = []
        for tx in transactions:
            key = (
                tx.get('Date'),
                tx.get('Description'),
                tx.get('Withdrawal_Amount'),
                tx.get('Deposit_Amount'),
                tx.get('Closing_Balance')
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(tx)
        return unique
    
    def _fallback_regex_parse(
        self, 
        text: str, 
        source_file: str
    ) -> None:
        """
        Fallback regex-based parsing for when LLM is unavailable.
        Attempts to identify common transaction patterns.
        """
        import re
        
        print("  📝 Using fallback regex parsing...")
        
        lines = text.splitlines()
        
        # Common date patterns
        date_patterns = [
            r'(\d{2}/\d{2}/\d{4})',  # DD/MM/YYYY
            r'(\d{2}/\d{2}/\d{2})',   # DD/MM/YY
            r'(\d{2}-\d{2}-\d{4})',   # DD-MM-YYYY
            r'(\d{4}-\d{2}-\d{2})',   # YYYY-MM-DD
        ]
        
        # Amount pattern
        amount_pattern = r'[\d,]+\.?\d{0,2}'
        
        for line_no, line in enumerate(lines, 1):
            line = line.strip()
            if not line or len(line) < 10:
                continue
            
            # Try each date pattern
            for date_pat in date_patterns:
                date_match = re.search(date_pat, line)
                if date_match:
                    # Found a date, try to extract amounts
                    amounts = re.findall(amount_pattern, line)
                    amounts = [self.clean_amount_string(a) for a in amounts]
                    amounts = [a for a in amounts if a is not None and a > 0]
                    
                    if amounts:
                        # Extract description (text between date and amounts)
                        description = line[date_match.end():].strip()
                        # Remove amounts from description
                        for amt in amounts:
                            description = description.replace(str(amt), '').strip()
                        
                        # Determine withdrawal vs deposit
                        withdrawal = None
                        deposit = None
                        balance = None
                        
                        if len(amounts) >= 3:
                            withdrawal = amounts[0] if amounts[0] > 0 else None
                            deposit = amounts[1] if amounts[1] > 0 else None
                            balance = amounts[-1]
                        elif len(amounts) == 2:
                            # Guess based on keywords
                            desc_lower = description.lower()
                            if any(w in desc_lower for w in ['cr', 'credit', 'neft cr', 'deposit']):
                                deposit = amounts[0]
                            else:
                                withdrawal = amounts[0]
                            balance = amounts[1]
                        elif len(amounts) == 1:
                            balance = amounts[0]
                        
                        tx = self.create_transaction_dict(
                            date=date_match.group(1),
                            description=description[:200],  # Limit length
                            reference="",
                            withdrawal_amt=withdrawal,
                            deposit_amt=deposit,
                            balance_amt=balance,
                            source_file=source_file,
                            line_ref=line_no
                        )
                        self.transactions.append(tx)
                        break
    
    def get_processing_stats(self) -> Optional[ProcessingStats]:
        """Get statistics from the last processing run."""
        return self.stats
    
    def estimate_processing_cost(
        self, 
        pdf_path: str
    ) -> Tuple[int, float]:
        """
        Estimate the cost of processing a PDF before actually processing.
        
        Args:
            pdf_path: Path to the PDF
            
        Returns:
            Tuple of (page_count, estimated_cost_usd)
        """
        import pdfplumber
        
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
        
        if self.llm_extractor and self.config.prefer_vision:
            cost = self.llm_extractor.estimate_cost(num_images=page_count)
        elif self.llm_extractor:
            # Estimate ~2000 chars per page of OCR text
            cost = self.llm_extractor.estimate_cost(text_length=page_count * 2000)
        else:
            cost = 0.0
        
        return page_count, cost


# Factory function for easy instantiation
def create_universal_parser(
    progress_callback: Optional[Callable] = None,
    use_llm: bool = True,
    prefer_vision: bool = False,
    llm_model: str = "gpt-4o-mini",
    max_pages: Optional[int] = None,
    use_table_structure: bool = True,
    min_table_transactions: Optional[int] = 5
) -> UniversalBankParser:
    """
    Create a configured universal parser.
    
    Args:
        progress_callback: Optional callback for progress updates
        use_llm: Whether to use LLM for extraction
        prefer_vision: Whether to send images directly to LLM
        llm_model: Which OpenAI model to use
        max_pages: Maximum pages to process (for SaaS limits)
        
    Returns:
        Configured UniversalBankParser instance
    """
    config = ProcessingConfig(
        use_llm=use_llm,
        prefer_vision=prefer_vision,
        llm_model=llm_model,
        max_pages=max_pages,
        use_table_structure=use_table_structure,
        min_table_transactions=min_table_transactions
    )
    return UniversalBankParser(progress_callback, config)
