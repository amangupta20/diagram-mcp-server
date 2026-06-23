FROM python:3.11-slim

# Install system dependencies for Matplotlib and Manim
RUN apt-get update && apt-get install -y \
    build-essential \
    pkg-config \
    python3-dev \
    libcairo2-dev \
    libpango1.0-dev \
    ffmpeg \
    texlive \
    texlive-latex-extra \
    texlive-fonts-extra \
    texlive-latex-recommended \
    texlive-science \
    tipa \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and project
COPY pyproject.toml .
COPY src/ src/

# Install the application
RUN pip install --no-cache-dir -e .

EXPOSE 8000

# Run SSE server
ENTRYPOINT ["python", "src/diagram_mcp/server.py", "--sse", "--host", "0.0.0.0", "--port", "8000"]
