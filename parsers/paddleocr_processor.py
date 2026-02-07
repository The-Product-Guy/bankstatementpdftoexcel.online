#!/usr/bin/env python3
"""
PaddleOCR Processor for Enhanced Table Recognition
Provides superior OCR accuracy for bank statements with table structure detection.
Supports PaddleOCR v2.x and v3.x APIs.
"""
import os
import gc
from typing import List, Dict, Any, Optional, Tuple
from PIL import Image
import numpy as np

# Prevent PaddleX from making slow network checks on startup
os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')

# Lazy imports to speed up app startup
_paddle_ocr = None
_table_engine = None


def get_paddleocr():
    """Lazy load PaddleOCR to reduce startup time."""
    global _paddle_ocr
    if _paddle_ocr is None:
        try:
            from paddleocr import PaddleOCR
            # PaddleOCR v3.x uses different parameter names than v2.x.
            # Try v3 API first, fall back to v2 on TypeError.
            try:
                _paddle_ocr = PaddleOCR(
                    use_textline_orientation=True,
                    lang='en',
                    text_det_thresh=0.3,
                    text_recognition_batch_size=6,
                )
            except TypeError:
                # Older PaddleOCR (v2.x) API
                _paddle_ocr = PaddleOCR(
                    use_angle_cls=True,
                    lang='en',
                    det_db_thresh=0.3,
                    rec_batch_num=6,
                )
        except ImportError:
            raise ImportError(
                "PaddleOCR not installed. Install with: pip install paddleocr"
            )
    return _paddle_ocr


def get_table_engine():
    """Lazy load PaddleOCR Table Structure Recognition."""
    global _table_engine
    if _table_engine is None:
        try:
            from paddleocr import PPStructure
            _table_engine = PPStructure(
                table=True,
                ocr=True,
                lang='en'
            )
        except ImportError:
            # Table structure recognition is optional
            _table_engine = None
    return _table_engine


class PaddleOCRProcessor:
    """
    Enhanced OCR processor using PaddleOCR.
    Provides better accuracy than Tesseract for complex table layouts.
    """
    
    def __init__(self, use_table_structure: bool = False):
        """
        Initialize the PaddleOCR processor.
        
        Args:
            use_table_structure: Whether to use table structure recognition
                               (more accurate but slower)
        """
        self.use_table_structure = use_table_structure
        self._ocr = None
        self._table = None
    
    @property
    def ocr(self):
        """Lazy load OCR engine."""
        if self._ocr is None:
            self._ocr = get_paddleocr()
        return self._ocr
    
    @property
    def table_engine(self):
        """Lazy load table engine."""
        if self._table is None and self.use_table_structure:
            self._table = get_table_engine()
        return self._table
    
    def process_image(self, image: Image.Image) -> List[Dict[str, Any]]:
        """
        Process a single image and extract text with bounding boxes.
        
        Args:
            image: PIL Image to process
            
        Returns:
            List of dicts with 'text', 'confidence', 'bbox' keys
        """
        # PaddleOCR expects 3-channel images; convert grayscale to RGB
        if image.mode != 'RGB':
            image = image.convert('RGB')
        # Convert PIL to numpy array
        img_array = np.array(image)
        
        # Run OCR - supports PaddleOCR v2.x and v3.x APIs
        try:
            if hasattr(self.ocr, 'predict'):
                result = self.ocr.predict(img_array)
            else:
                try:
                    result = self.ocr.ocr(img_array, cls=True)
                except TypeError:
                    result = self.ocr.ocr(img_array)
        except ImportError:
            raise
        except Exception as exc:
            raise RuntimeError(f"PaddleOCR processing failed: {exc}") from exc

        if not result:
            return []

        # ---- Normalize results from both v2 and v3 formats ----
        extracted = []
        try:
            first_item = result[0]
        except (IndexError, TypeError):
            return []
        if first_item is None:
            return []

        # v3.x returns [{"rec_texts": [...], "rec_scores": [...], "dt_polys": [...]}]
        if isinstance(first_item, dict):
            texts = first_item.get('rec_texts') or first_item.get('texts') or []
            scores = first_item.get('rec_scores') or first_item.get('scores') or []
            polys = first_item.get('dt_polys') or first_item.get('polys') or []
            for i, text in enumerate(texts):
                if not text or not str(text).strip():
                    continue
                conf = float(scores[i]) if i < len(scores) else 0.0
                if i < len(polys):
                    poly = polys[i]
                    try:
                        xs = [float(p[0]) for p in poly]
                        ys = [float(p[1]) for p in poly]
                        extracted.append({
                            'text': str(text),
                            'confidence': conf,
                            'bbox': (min(xs), min(ys), max(xs), max(ys)),
                            'bbox_polygon': poly,
                        })
                    except (IndexError, TypeError, ValueError):
                        continue
            return extracted

        # v2.x returns [[line1, line2, ...]] or [line1, line2, ...]
        # Each line is [bbox_polygon, (text, confidence)]
        page_result = first_item
        if isinstance(first_item, (list, tuple)) and len(first_item) >= 2:
            inner = first_item[0]
            if isinstance(inner, (list, tuple)) and len(inner) == 4:
                if isinstance(inner[0], (list, tuple)) and len(inner[0]) == 2:
                    page_result = result  # flat format

        if not page_result or not isinstance(page_result, (list, tuple)):
            return []

        for line in page_result:
            try:
                if line is None or not isinstance(line, (list, tuple)) or len(line) < 2:
                    continue
                bbox = line[0]
                text_info = line[1]
                if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                    continue
                if not isinstance(text_info, (list, tuple)) or len(text_info) < 2:
                    continue
                text = text_info[0]
                confidence = text_info[1]
                if not isinstance(text, str) or not text:
                    continue
                if not isinstance(confidence, (int, float)):
                    confidence = 0.0
                valid_points = [p for p in bbox if isinstance(p, (list, tuple)) and len(p) >= 2]
                if len(valid_points) < 4:
                    continue
                x_coords = [p[0] for p in valid_points]
                y_coords = [p[1] for p in valid_points]
                extracted.append({
                    'text': text,
                    'confidence': confidence,
                    'bbox': (min(x_coords), min(y_coords), max(x_coords), max(y_coords)),
                    'bbox_polygon': bbox,
                })
            except (IndexError, TypeError, ValueError):
                continue

        return extracted
    
    def process_image_to_text(self, image: Image.Image) -> str:
        """
        Process image and return plain text (similar to Tesseract output).
        Maintains spatial ordering (top to bottom, left to right).
        """
        results = self.process_image(image)
        
        if not results:
            return ""
        
        # Sort by vertical position first, then horizontal
        sorted_results = sorted(results, key=lambda x: (x['bbox'][1], x['bbox'][0]))
        
        lines = []
        current_line = []
        current_y = None
        line_threshold = 15  # Pixels threshold for same line
        
        for item in sorted_results:
            y_pos = item['bbox'][1]
            
            if current_y is None:
                current_y = y_pos
            
            # Check if this is a new line
            if abs(y_pos - current_y) > line_threshold:
                # Sort current line by x position and join
                current_line.sort(key=lambda x: x['bbox'][0])
                line_text = '  '.join([r['text'] for r in current_line])
                lines.append(line_text)
                current_line = []
                current_y = y_pos
            
            current_line.append(item)
        
        # Don't forget the last line
        if current_line:
            current_line.sort(key=lambda x: x['bbox'][0])
            line_text = '  '.join([r['text'] for r in current_line])
            lines.append(line_text)
        
        return '\n'.join(lines)
    
    def detect_table_structure(self, image: Image.Image) -> Optional[List[Dict[str, Any]]]:
        """
        Detect table structure in the image.
        Returns table cells with their positions and content.
        
        Args:
            image: PIL Image to process
            
        Returns:
            List of table dicts with 'bbox', 'html', 'cells' keys, or None
        """
        if not self.use_table_structure or self.table_engine is None:
            return None
        
        try:
            if image.mode != 'RGB':
                image = image.convert('RGB')
            img_array = np.array(image)
            
            result = self.table_engine(img_array)
            
            # Validate result
            if not result:
                return None
            
            if not isinstance(result, (list, tuple)):
                return None
            
            tables = []
            for item in result:
                try:
                    # Validate item is a dict
                    if not isinstance(item, dict):
                        continue
                    
                    # Check if it's a table
                    if item.get('type') != 'table':
                        continue
                    
                    # Safely extract res dict
                    res = item.get('res')
                    if not isinstance(res, dict):
                        res = {}
                    
                    tables.append({
                        'bbox': item.get('bbox'),
                        'html': res.get('html', ''),
                        'cells': res.get('cell_bbox', [])
                    })
                except (TypeError, KeyError, AttributeError):
                    # Skip malformed items
                    continue
            
            return tables if tables else None
            
        except Exception as e:
            # Catch any PPStructure errors including tuple index errors
            print(f"    ⚠️ Table structure detection error: {e}")
            return None
    
    def extract_with_coordinates(
        self, 
        image: Image.Image,
        confidence_threshold: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Extract text with detailed coordinate information.
        Useful for understanding table column structure.
        
        Args:
            image: PIL Image to process
            confidence_threshold: Minimum confidence to include result
            
        Returns:
            List of text elements with coordinates and confidence
        """
        results = self.process_image(image)
        
        # Filter by confidence
        filtered = [
            r for r in results 
            if r['confidence'] >= confidence_threshold
        ]
        
        return filtered
    
    def get_column_structure(
        self, 
        results: List[Dict[str, Any]],
        num_columns: Optional[int] = None
    ) -> List[List[Dict[str, Any]]]:
        """
        Attempt to group OCR results into columns.
        
        Args:
            results: OCR results with bounding boxes
            num_columns: Expected number of columns (auto-detect if None)
            
        Returns:
            List of columns, each containing sorted text items
        """
        if not results:
            return []
        
        # Get all x-coordinates
        x_positions = [(r['bbox'][0] + r['bbox'][2]) / 2 for r in results]
        
        if num_columns is None:
            # Try to auto-detect columns based on x-position clustering
            # Simple approach: look for gaps in x-positions
            sorted_x = sorted(set(x_positions))
            gaps = []
            for i in range(1, len(sorted_x)):
                gap = sorted_x[i] - sorted_x[i-1]
                gaps.append((gap, sorted_x[i-1], sorted_x[i]))
            
            # Find significant gaps (larger than 50 pixels)
            significant_gaps = [g for g in gaps if g[0] > 50]
            num_columns = len(significant_gaps) + 1
        
        if num_columns <= 1:
            return [sorted(results, key=lambda x: x['bbox'][1])]
        
        # Calculate column boundaries
        min_x = min(x_positions)
        max_x = max(x_positions)
        col_width = (max_x - min_x) / num_columns
        
        # Assign items to columns
        columns = [[] for _ in range(num_columns)]
        for r in results:
            center_x = (r['bbox'][0] + r['bbox'][2]) / 2
            col_idx = min(int((center_x - min_x) / col_width), num_columns - 1)
            columns[col_idx].append(r)
        
        # Sort each column by y-position
        for col in columns:
            col.sort(key=lambda x: x['bbox'][1])
        
        return columns


def process_pdf_page(
    page_image: Image.Image,
    preprocess: bool = True,
    use_table_structure: bool = False
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Convenience function to process a single PDF page.
    
    Args:
        page_image: PIL Image of the page
        preprocess: Whether to preprocess image for better OCR
        use_table_structure: Whether to detect table structure
        
    Returns:
        Tuple of (extracted_text, detailed_results)
    """
    if preprocess:
        from .image_preprocessor import preprocess_for_ocr
        page_image = preprocess_for_ocr(page_image)
    
    processor = PaddleOCRProcessor(use_table_structure=use_table_structure)
    
    text = processor.process_image_to_text(page_image)
    detailed = processor.extract_with_coordinates(page_image)
    
    # Clean up
    gc.collect()
    
    return text, detailed
