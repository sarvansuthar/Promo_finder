# Hugging Face Spaces (Docker SDK) / any container host.
# Local test:  docker build -t promo-ranker . && docker run -p 7860:7860 promo-ranker
FROM python:3.12-slim

# HF Spaces run containers as uid 1000; give that user a writable home for .cache/ and outputs
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

EXPOSE 7860
# XSRF/CORS off: HF serves the app inside an iframe, where Streamlit's upload check otherwise returns 403
CMD ["streamlit", "run", "app.py", "--server.port=7860", "--server.address=0.0.0.0", \
     "--server.enableXsrfProtection=false", "--server.enableCORS=false", "--server.headless=true"]
