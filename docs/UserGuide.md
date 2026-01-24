# Universal Bank Statement Converter - User Guide

## Overview

The Universal Bank Statement Converter transforms PDF bank statements into structured Excel files using advanced AI. It supports statements from **any bank** worldwide.

## Features

- **Universal Parsing**: Upload statements from HDFC, ICICI, KVB, Chase, HSBC, or any local bank.
- **Accurate Extraction**: Uses OpenAI's GPT-4o-mini to understand transaction rows, dates, and amounts.
- **Excel Output**: Downloads a clean, formatted Excel file ready for analysis.
- **Secure**: Files are processed ephemerally and not stored permanently.

## How to Use

1.  **Open the Application**: Navigate to the web interface (e.g., `https://your-app.railway.app`).
2.  **Select Bank**: Choose "Universal (Any Bank)" for best results.
3.  **Upload PDF**: Click "Choose File" and select your PDF bank statement.
    *   *Note: Max file size is 20MB.*
4.  **API Key (Optional)**: If you possess your own OpenAI API Key, you may enter it to bypass shared limits.
5.  **Convert**: Click the "Convert to Excel" button.
6.  **Wait for Processing**:
    *   A progress bar will appear.
    *   Standard statements take **1-3 minutes** to process depending on pages.
    *   *Do not close the tab while processing.*
7.  **Download**: Once complete, a green "Download Excel" button will appear.

## Troubleshooting

*   **Stuck at 0%?**: Refresh the page and try again. Ensure your internet connection is stable.
*   **"Error: File too large"**: Please split your PDF into smaller chunks (e.g., < 50 pages) using a PDF splitter.
*   **Missing Transactions**: Use the "Report Issue" link (if configured) or ensure the PDF is legible.

## Privacy & Security

*   uploaded files are **encrypted** in transit.
*   Files are **deleted** from the server immediately after processing.
*   No human reviews your financial data.

---
**Support**: For technical support, contact the administrator.
