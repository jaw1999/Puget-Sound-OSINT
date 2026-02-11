FROM python:3.10-slim

# System deps for OpenCV
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgl1-mesa-glx libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU-only PyTorch first to avoid pulling CUDA packages
RUN pip install --no-cache-dir \
    torch==2.1.2+cpu \
    --index-url https://download.pytorch.org/whl/cpu

# Install remaining dependencies (torch already satisfied)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and model files
COPY . .

# Create runtime directories
RUN mkdir -p /app/captures /app/reports /app/logs

EXPOSE 8080

CMD ["python3", "run.py", "--config", "config/settings.yaml", "--log-file", "logs/osint.log", "--port", "8080"]
