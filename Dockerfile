# One image supports both CPU and NVIDIA execution. CUDA libraries add size but avoid split tags.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04 AS runtime
ARG TARGETARCH
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
RUN apt-get update && apt-get install -y --no-install-recommends python3.12 python3-pip ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY subgen ./subgen
RUN python3 -m pip install --break-system-packages .
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/health || exit 1
CMD ["python3", "-m", "uvicorn", "subgen.main:app", "--host", "0.0.0.0", "--port", "8080"]

