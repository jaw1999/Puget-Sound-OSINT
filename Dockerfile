FROM python:3.10-slim

# System deps for OpenCV
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU-only PyTorch first to avoid pulling CUDA packages
RUN pip install --no-cache-dir "numpy<2" && \
    pip install --no-cache-dir \
    torch==2.1.2 torchvision==0.16.2 \
    --index-url https://download.pytorch.org/whl/cpu

# Install remaining dependencies, skipping torch (already installed as CPU-only)
COPY requirements.txt .
RUN grep -iv '^torch' requirements.txt > requirements-notorch.txt && \
    echo "numpy<2" > /tmp/constraints.txt && \
    pip install --no-cache-dir -c /tmp/constraints.txt -r requirements-notorch.txt

# Copy application code and model files
COPY . .

# Create runtime directories
RUN mkdir -p /app/captures /app/reports /app/logs

EXPOSE 8080

CMD ["python3", "run.py", "--config", "config/settings.yaml", "--log-file", "logs/osint.log", "--port", "8080"]
