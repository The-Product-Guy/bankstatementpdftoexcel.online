#!/usr/bin/env python3
"""
Image Preprocessing Utilities for Better OCR Results
Handles deskewing, contrast enhancement, and noise reduction
"""
import cv2
import numpy as np
from PIL import Image
from typing import Tuple, Optional


def pil_to_cv2(pil_image: Image.Image) -> np.ndarray:
    """Convert PIL Image to OpenCV format."""
    if pil_image.mode == 'RGB':
        return cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
    elif pil_image.mode == 'L':
        return np.array(pil_image)
    else:
        return cv2.cvtColor(np.array(pil_image.convert('RGB')), cv2.COLOR_RGB2BGR)


def cv2_to_pil(cv2_image: np.ndarray) -> Image.Image:
    """Convert OpenCV image to PIL format."""
    if len(cv2_image.shape) == 2:
        return Image.fromarray(cv2_image)
    return Image.fromarray(cv2.cvtColor(cv2_image, cv2.COLOR_BGR2RGB))


def deskew_image(image: np.ndarray) -> np.ndarray:
    """
    Deskew a scanned document image.
    Detects rotation angle and corrects it.
    """
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()
    
    # Threshold to get binary image
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    
    # Find all coordinates of non-zero pixels
    coords = np.column_stack(np.where(binary > 0))
    
    if len(coords) < 100:
        return image  # Not enough points to determine angle
    
    # Find minimum area rectangle
    try:
        rect = cv2.minAreaRect(coords)
        angle = rect[-1]
        
        # Adjust angle
        if angle < -45:
            angle = 90 + angle
        elif angle > 45:
            angle = angle - 90
        
        # Only rotate if angle is significant but not too extreme
        if abs(angle) > 0.5 and abs(angle) < 15:
            (h, w) = image.shape[:2]
            center = (w // 2, h // 2)
            rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            rotated = cv2.warpAffine(
                image, rotation_matrix, (w, h),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_REPLICATE
            )
            return rotated
    except Exception:
        pass
    
    return image


def enhance_contrast(image: np.ndarray) -> np.ndarray:
    """
    Enhance image contrast for better OCR.
    Uses CLAHE (Contrast Limited Adaptive Histogram Equalization).
    """
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()
    
    # Apply CLAHE
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    
    return enhanced


def remove_noise(image: np.ndarray) -> np.ndarray:
    """
    Remove noise from image using bilateral filtering.
    Preserves edges while smoothing noise.
    """
    if len(image.shape) == 3:
        return cv2.bilateralFilter(image, 9, 75, 75)
    else:
        return cv2.bilateralFilter(image, 9, 75, 75)


def binarize_image(image: np.ndarray, adaptive: bool = True) -> np.ndarray:
    """
    Convert image to binary (black and white).
    Uses adaptive thresholding for better results on varied backgrounds.
    """
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()
    
    if adaptive:
        # Adaptive thresholding works better for documents with varying lighting
        binary = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            11, 2
        )
    else:
        # Otsu's thresholding
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    return binary


def preprocess_for_ocr(
    image: Image.Image,
    deskew: bool = True,
    enhance: bool = True,
    denoise: bool = True,
    binarize: bool = False
) -> Image.Image:
    """
    Main preprocessing pipeline for OCR.
    Applies selected transformations to improve OCR accuracy.
    
    Args:
        image: PIL Image to preprocess
        deskew: Whether to correct rotation
        enhance: Whether to enhance contrast
        denoise: Whether to remove noise
        binarize: Whether to convert to binary (use for very noisy images)
    
    Returns:
        Preprocessed PIL Image
    """
    # Convert to OpenCV format
    cv_image = pil_to_cv2(image)
    
    # Apply preprocessing steps
    if deskew:
        cv_image = deskew_image(cv_image)
    
    if denoise:
        cv_image = remove_noise(cv_image)
    
    if enhance:
        cv_image = enhance_contrast(cv_image)
    
    if binarize:
        cv_image = binarize_image(cv_image)
    
    # Convert back to PIL
    return cv2_to_pil(cv_image)


def get_image_quality_score(image: Image.Image) -> float:
    """
    Estimate image quality for OCR.
    Returns score from 0 (poor) to 1 (excellent).
    """
    cv_image = pil_to_cv2(image)
    
    if len(cv_image.shape) == 3:
        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
    else:
        gray = cv_image
    
    # Calculate Laplacian variance (measure of focus/sharpness)
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    
    # Normalize to 0-1 range (empirical thresholds)
    # Very blurry: < 100, Sharp: > 500
    sharpness_score = min(laplacian_var / 500, 1.0)
    
    # Calculate contrast score
    contrast = gray.std() / 128  # Normalize by half of max value
    contrast_score = min(contrast, 1.0)
    
    # Combined score
    quality_score = (sharpness_score * 0.6 + contrast_score * 0.4)
    
    return round(quality_score, 2)
