FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential tesseract-ocr

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000"]