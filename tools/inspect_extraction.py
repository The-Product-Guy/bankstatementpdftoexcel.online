import os
import sys

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers.universal_parser import create_universal_parser

def inspect(pdf_path):
    print(f"Inspecting: {pdf_path}")
    parser = create_universal_parser(use_llm=False)
    
    # We want to see the text the parser sees
    # The parser has private methods, so we'll inspect via a subclass or just calling internal methods if we're naughty
    # Or we can just use the public .parse() and add print statements to the parser?
    # Let's add a method to getting debug info to the parser, or just hack it here.
    
    # Better: let's use the actual components
    from parsers.paddleocr_processor import PaddleOCRProcessor
    from pdf2image import convert_from_path
    
    try:
        images = convert_from_path(pdf_path)
        processor = PaddleOCRProcessor()
        
        print(f"\n--- Page 1 ({len(images)} pages total) ---")
        text = processor.process_image_to_text(images[0])
        print(text)
        print("\n--- End Page 1 ---")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        inspect(sys.argv[1])
    else:
        print("Usage: python tools/inspect_extraction.py <pdf_file>")
