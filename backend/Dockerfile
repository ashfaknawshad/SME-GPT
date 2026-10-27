FROM python:3.12-slim

# System libraries the pipeline needs at runtime:
#   poppler-utils — pdf2image (PDF -> page images)
#   libglib2.0-0 / libgl1 — OpenCV (cv2) shared-object dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    libglib2.0-0 \
    libgl1 \
 && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces runs the container as UID 1000 and expects a writable
# HOME for model/cache downloads. Running non-root is good practice on any host,
# so we do it everywhere, not just on Spaces.
RUN useradd -m -u 1000 appuser
USER appuser

ENV HOME=/home/appuser \
    PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    POPPLER_PATH=/usr/bin \
    HF_HOME=/home/appuser/.cache/huggingface

WORKDIR /home/appuser/app

COPY --chown=appuser:appuser requirements.txt .

# LOCAL_EMBEDDINGS=false builds a slim image without sentence-transformers /
# PyTorch, for servers that embed on the Colab GPU instead
# (EMBEDDING_ENGINE=colab in backend/.env). Saves ~1 GB of image and RAM.
#   docker build --build-arg LOCAL_EMBEDDINGS=false -t sme-gpt-backend .
ARG LOCAL_EMBEDDINGS=true

# Generous timeout/retries so a slow network doesn't fail the build midway
# through a large wheel (PyTorch is ~800 MB).
RUN if [ "$LOCAL_EMBEDDINGS" = "true" ]; then \
      pip install --no-cache-dir --user --timeout 300 --retries 10 -r requirements.txt; \
    else \
      grep -v '^sentence-transformers' requirements.txt > /tmp/requirements-slim.txt && \
      pip install --no-cache-dir --user --timeout 300 --retries 10 -r /tmp/requirements-slim.txt; \
    fi

# Bake the embedding model into the image so the first request doesn't stall on
# a ~470 MB download, and so it doesn't depend on ephemeral runtime storage.
RUN if [ "$LOCAL_EMBEDDINGS" = "true" ]; then \
      python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"; \
    fi

COPY --chown=appuser:appuser . .

# Runtime dirs the app writes to (relative to WORKDIR). Creating them here,
# owned by appuser, means Docker volumes mounted on them inherit that ownership
# instead of being root-owned and unwritable.
RUN mkdir -p saved_documents incoming_json temp_processing

# 7860 is the Hugging Face Spaces default; other hosts (Cloud Run, Render) inject
# their own $PORT, which the shell-form CMD picks up.
# --proxy-headers: behind a reverse proxy (Caddy), take the client IP from
# X-Forwarded-For so per-IP rate limiting sees real users, not the proxy.
# Trusting any forwarder is safe only because the container port is never
# published -- the proxy is the sole way in.
EXPOSE 7860
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips="*"
