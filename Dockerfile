FROM python:3.10-slim

WORKDIR /app

# Install system dependencies (optional if you need them)
RUN apt-get update && apt-get install -y build-essential

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Copy .env into the container
# COPY .env .env

# Set environment variables
ENV PYTHONUNBUFFERED=1

CMD ["python", "backend/rag_query.py"]