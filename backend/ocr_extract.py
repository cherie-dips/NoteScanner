# ocr_extract.py
import pytesseract
from PIL import Image

def extract_text_from_image(image_path: str) -> str:
    image = Image.open(image_path)
    text = pytesseract.image_to_string(image)
    return text

if __name__ == "__main__":
    text = extract_text_from_image("data/sample_note.jpeg")
    print(text)