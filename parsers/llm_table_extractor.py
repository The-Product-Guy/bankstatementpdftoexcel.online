#!/usr/bin/env python3
"""
LLM-Based Table Extraction using OpenAI
Intelligently extracts structured transaction data from bank statements
Uses CSV output format for reliability and dynamic column detection
"""
import logging
import os
import csv
import io
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Lazy imports for OpenAI
_openai_client = None


@dataclass
class ColumnMapping:
    """Detected column structure from the statement."""
    columns: List[str]  # Original column names from the document
    date_col: Optional[str] = None
    description_col: Optional[str] = None
    reference_col: Optional[str] = None
    debit_col: Optional[str] = None
    credit_col: Optional[str] = None
    balance_col: Optional[str] = None


@dataclass
class ExtractionResult:
    """Result of LLM extraction."""
    transactions: List[Dict[str, Any]]
    raw_response: str
    model_used: str
    tokens_used: int
    success: bool
    error_message: Optional[str] = None
    column_mapping: Optional[ColumnMapping] = None


def get_openai_client():
    """Get or create OpenAI client."""
    global _openai_client
    if _openai_client is None:
        try:
            from openai import OpenAI
            api_key = os.environ.get('OPENAI_API_KEY')
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY environment variable not set. "
                    "Set it with: export OPENAI_API_KEY='your-key'"
                )
            _openai_client = OpenAI(api_key=api_key, timeout=60.0)
        except ImportError:
            raise ImportError("OpenAI not installed. Install with: pip install openai")
    return _openai_client


# Prompt to detect column structure from first page
COLUMN_DETECTION_PROMPT = """Analyze this bank statement text and identify the column headers used for transactions.

Look for column headers like: Date, Value Date, Transaction Date, Description, Narration, Particulars, Reference, Ref No, Cheque No, Debit, Credit, Withdrawal, Deposit, Amount, Balance, etc.

Return a simple comma-separated list of the column names you found, in the order they appear from left to right. Only include columns that contain transaction data.

TEXT:
{text}

Respond with ONLY the comma-separated column names, nothing else. Example: Date,Description,Debit,Credit,Balance"""


# Prompt for extracting transactions as CSV
EXTRACTION_PROMPT_CSV = """Extract all bank transactions from this text and return them as CSV data.

The columns in this statement are: {columns}

For each transaction row, extract the values in the same column order. Rules:
1. One transaction per line
2. Use comma as delimiter
3. Wrap values containing commas in double quotes
4. Use empty string for missing values
5. Remove currency symbols (₹, Rs, INR) and commas from numbers
6. Keep dates in their original format
7. Skip header rows, subtotals, and summary lines
8. If a transaction spans multiple lines, combine into one row

TEXT:
{text}

Respond with ONLY the CSV data rows, no headers, no explanations."""


class LLMTableExtractor:
    """
    Extract structured transaction data using OpenAI's LLM models.
    Uses CSV output for reliability and dynamic column detection.
    """
    
    # Model pricing (USD per 1K tokens)
    MODELS = {
        'gpt-4o': {'context': 128000, 'cost_per_1k_input': 0.005, 'cost_per_1k_output': 0.015},
        'gpt-4o-mini': {'context': 128000, 'cost_per_1k_input': 0.00015, 'cost_per_1k_output': 0.0006},
        'gpt-4-turbo': {'context': 128000, 'cost_per_1k_input': 0.01, 'cost_per_1k_output': 0.03},
    }
    
    def __init__(
        self, 
        model: str = "gpt-4o-mini",
        max_retries: int = 2,
        temperature: float = 0.1
    ):
        self.model = model
        self.max_retries = max_retries
        self.temperature = temperature
        self._client = None
        self._column_mapping: Optional[ColumnMapping] = None
        
        if model not in self.MODELS:
            raise ValueError(f"Unknown model: {model}. Available: {list(self.MODELS.keys())}")
    
    @property
    def client(self):
        """Lazy load OpenAI client."""
        if self._client is None:
            self._client = get_openai_client()
        return self._client
    
    def detect_columns(self, first_page_text: str) -> ColumnMapping:
        """
        Detect column structure from the first page of the statement.
        
        Args:
            first_page_text: Text from the first page containing headers
            
        Returns:
            ColumnMapping with detected column names
        """
        prompt = COLUMN_DETECTION_PROMPT.format(text=first_page_text[:3000])  # Limit to first 3K chars
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200
            )
            
            raw_response = response.choices[0].message.content.strip()
            columns = [col.strip() for col in raw_response.split(',') if col.strip()]
            
            # Create mapping with intelligent column identification
            mapping = ColumnMapping(columns=columns)
            
            for col in columns:
                col_lower = col.lower()
                
                # Date columns (TXN DT, VALUE_DT, Date, Transaction Date, etc.)
                if any(x in col_lower for x in ['date', 'dt', 'txn dt', 'value_dt']) and not mapping.date_col:
                    mapping.date_col = col
                
                # Description/Narration columns
                if any(x in col_lower for x in ['desc', 'narr', 'partic', 'remark']):
                    mapping.description_col = col
                
                # Reference columns
                if any(x in col_lower for x in ['ref', 'cheq', 'chq', 'utr', 'txn']):
                    mapping.reference_col = col
                
                # Debit/Withdrawal columns  
                if any(x in col_lower for x in ['debit', 'withdraw', 'dr']):
                    mapping.debit_col = col
                
                # Credit/Deposit columns
                if any(x in col_lower for x in ['credit', 'deposit', 'cr']):
                    mapping.credit_col = col
                
                # Balance columns
                if 'balance' in col_lower or 'bal' in col_lower:
                    mapping.balance_col = col
            
            self._column_mapping = mapping
            return mapping
            
        except Exception as e:
            # Default fallback columns
            default = ColumnMapping(
                columns=['Date', 'Description', 'Reference', 'Debit', 'Credit', 'Balance'],
                date_col='Date',
                description_col='Description',
                reference_col='Reference',
                debit_col='Debit',
                credit_col='Credit',
                balance_col='Balance'
            )
            self._column_mapping = default
            return default
    
    def extract_from_text(
        self, 
        text: str,
        column_mapping: Optional[ColumnMapping] = None
    ) -> ExtractionResult:
        """
        Extract transactions from text using LLM with CSV output.
        
        Args:
            text: Bank statement text to extract from
            column_mapping: Column mapping from detect_columns (or auto-detected)
            
        Returns:
            ExtractionResult with parsed transactions
        """
        # Use provided mapping or previously detected one
        mapping = column_mapping or self._column_mapping
        
        if not mapping:
            # Auto-detect from the beginning of the text
            mapping = self.detect_columns(text[:3000])
        
        columns_str = ', '.join(mapping.columns)
        prompt = EXTRACTION_PROMPT_CSV.format(columns=columns_str, text=text)
        
        return self._call_llm(prompt, mapping)
    
    def _call_llm(
        self, 
        prompt: str,
        mapping: ColumnMapping
    ) -> ExtractionResult:
        """Make the actual LLM API call with retries."""
        last_error = None
        
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self.temperature,
                    max_tokens=8192  # CSV is more compact, 8K should be plenty
                )
                
                raw_response = response.choices[0].message.content
                tokens_used = response.usage.total_tokens if response.usage else 0
                
                # Parse CSV response
                transactions = self._parse_csv_response(raw_response, mapping)
                
                return ExtractionResult(
                    transactions=transactions,
                    raw_response=raw_response,
                    model_used=self.model,
                    tokens_used=tokens_used,
                    success=True,
                    column_mapping=mapping
                )
                
            except Exception as e:
                last_error = str(e)
                if attempt < self.max_retries:
                    continue
        
        return ExtractionResult(
            transactions=[],
            raw_response="",
            model_used=self.model,
            tokens_used=0,
            success=False,
            error_message=last_error
        )
    
    def _parse_csv_response(
        self, 
        response: str,
        mapping: ColumnMapping
    ) -> List[Dict[str, Any]]:
        """Parse CSV response to transaction list."""
        transactions = []
        
        # Clean up response
        response = response.strip()
        
        # Remove markdown code blocks if present
        if '```' in response:
            # Find content between ``` markers
            lines = response.split('\n')
            new_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith('```'):
                    in_block = not in_block
                    continue
                if not in_block or (in_block and line.strip()):
                    new_lines.append(line)
            response = '\n'.join(new_lines) if new_lines else response
        
        # Parse CSV
        try:
            reader = csv.reader(io.StringIO(response))
            
            for row in reader:
                if not row or len(row) < 2:  # Skip empty or too-short rows
                    continue
                
                # Skip if first value looks like a header
                first_val = str(row[0]).lower().strip()
                if any(h in first_val for h in ['date', 'txn', 'trans', 'sl']):
                    continue
                
                tx = self._row_to_transaction(row, mapping)
                if tx and tx.get('Date'):  # Only include if we got a date
                    transactions.append(tx)
                    
        except Exception as e:
            # Fallback: try line-by-line parsing
            for line in response.strip().split('\n'):
                if not line.strip():
                    continue
                try:
                    row = next(csv.reader([line]))
                    tx = self._row_to_transaction(row, mapping)
                    if tx and tx.get('Date'):
                        transactions.append(tx)
                except:
                    continue
        
        return transactions
    
    def _row_to_transaction(
        self, 
        row: List[str],
        mapping: ColumnMapping
    ) -> Optional[Dict[str, Any]]:
        """Convert a CSV row to a transaction dictionary."""
        if len(row) < 2:
            return None
        
        tx = {}
        columns = mapping.columns
        
        # Map values to columns
        for i, value in enumerate(row):
            if i >= len(columns):
                break
            
            col = columns[i]
            value = str(value).strip()
            
            # Map to standard field names
            if col == mapping.date_col:
                tx['Date'] = value
            elif col == mapping.description_col:
                tx['Description'] = value
            elif col == mapping.reference_col:
                tx['Reference_Number'] = value
            elif col == mapping.debit_col:
                tx['Withdrawal_Amount'] = self._parse_amount(value)
            elif col == mapping.credit_col:
                tx['Deposit_Amount'] = self._parse_amount(value)
            elif col == mapping.balance_col:
                tx['Closing_Balance'] = self._parse_amount(value)
            else:
                # Store unknown columns with original names
                tx[col] = value
        
        # Calculate Transaction_Amount
        withdrawal = tx.get('Withdrawal_Amount')
        deposit = tx.get('Deposit_Amount')
        
        if deposit:
            tx['Transaction_Amount'] = deposit
        elif withdrawal:
            tx['Transaction_Amount'] = -withdrawal
        else:
            tx['Transaction_Amount'] = 0
        
        return tx
    
    def _parse_amount(self, value: Any) -> Optional[float]:
        """Parse amount value to float."""
        if value is None or value == '' or value == 'null' or value == '-':
            return None
        
        if isinstance(value, (int, float)):
            return float(value) if value != 0 else None
        
        if isinstance(value, str):
            # Remove currency symbols, commas, and spaces
            cleaned = re.sub(r'[₹$€£,\s]', '', value)
            # Handle negative amounts in parentheses
            if cleaned.startswith('(') and cleaned.endswith(')'):
                cleaned = '-' + cleaned[1:-1]
            try:
                amount = float(cleaned)
                return amount if amount != 0 else None
            except ValueError:
                return None
        
        return None
    
    def estimate_cost(self, text_length: int = 0, num_pages: int = 0) -> float:
        """
        Estimate API cost for processing.
        
        Args:
            text_length: Approximate character count of text
            num_pages: Number of pages (alternative to text_length)
            
        Returns:
            Estimated cost in USD
        """
        model_info = self.MODELS[self.model]
        
        # Rough token estimation
        if text_length:
            input_tokens = text_length / 4  # ~4 chars per token
        else:
            input_tokens = num_pages * 700  # ~700 tokens per page
        
        input_tokens += 500  # Prompt overhead
        output_tokens = num_pages * 200 if num_pages else 500  # CSV output estimate
        
        input_cost = (input_tokens / 1000) * model_info['cost_per_1k_input']
        output_cost = (output_tokens / 1000) * model_info['cost_per_1k_output']
        
        return round(input_cost + output_cost, 4)


# Convenience function for quick extraction
def extract_transactions_from_text(
    text: str,
    model: str = "gpt-4o-mini"
) -> List[Dict[str, Any]]:
    """Quick extraction from text."""
    extractor = LLMTableExtractor(model=model)
    result = extractor.extract_from_text(text)
    return result.transactions if result.success else []
