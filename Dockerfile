# Dockerfile for Hamid's Pulse Auto News
# Optional containerized deployment

FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p secrets data logs

# Expose web UI port
EXPOSE 8000

# Set environment
ENV PYTHONUNBUFFERED=1

# Run the application
CMD ["python", "main.py"]
