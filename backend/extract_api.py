"""Local text extraction from images (fallback when Sarvam Vision is unavailable). PDFs: see pages.py."""
import io

from PIL import Image
import pytesseract


def extract_text_from_image_bytes(data: bytes) -> str:
    img = Image.open(io.BytesIO(data))
    return pytesseract.image_to_string(img)
