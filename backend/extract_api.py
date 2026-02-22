"""Extract text from PDFs and images. Uses notes_root as the user's folder (e.g. user_notes/<user_id>)."""
import os
from PIL import Image
import pytesseract
import fitz  # PyMuPDF


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes in memory. Does not write to disk."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        text = ""
        for page in doc:
            text += page.get_text()
        return text
    finally:
        doc.close()


def extract_text(notes_root: str, subject: str):
    """
    Extract text from PDFs and images in notes_root/subject.
    Saves .txt files next to originals. Returns extracted content for saving to MongoDB.
    """
    folder_path = os.path.join(notes_root, subject)
    if not os.path.exists(folder_path):
        return {"error": "Folder does not exist.", "processed_files": [], "extracted_texts": {}}

    extracted = {}
    processed_files = []

    for fname in os.listdir(folder_path):
        fpath = os.path.join(folder_path, fname)
        if os.path.isfile(fpath):
            try:
                if fname.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
                    print(f"🔄 Extracting text from image: {fname}")
                    text = pytesseract.image_to_string(Image.open(fpath))
                    extracted[fname] = text
                elif fname.lower().endswith('.pdf'):
                    print(f"🔄 Extracting text from PDF: {fname}")
                    pdf = fitz.open(fpath)
                    pdf_text = ""
                    for page in pdf:
                        pdf_text += page.get_text()
                    pdf.close()
                    extracted[fname] = pdf_text
                else:
                    print(f"⚠️ Skipping unsupported file: {fname}")
                    continue

                # Save extracted text to .txt file (local only; DB is updated by caller)
                txt_filename = os.path.splitext(fname)[0] + ".txt"
                txt_path = os.path.join(folder_path, txt_filename)
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(extracted[fname])

                processed_files.append({
                    "original_file": fname,
                    "text_file": txt_filename,
                    "text_length": len(extracted[fname]),
                })
                print(f"✅ Saved text to {txt_filename}")

            except Exception as e:
                print(f"❌ Error processing {fname}: {str(e)}")
                continue

    return {
        "message": f"Extracted text from {len(processed_files)} files",
        "processed_files": processed_files,
        "extracted_texts": extracted,
    }
