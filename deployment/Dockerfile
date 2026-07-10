FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY static/ ./static/
COPY use_cases/ ./use_cases/
COPY formats/ ./formats/
COPY generators/ ./generators/
COPY banks/ ./banks/

COPY start.sh .
RUN chmod +x start.sh && mkdir -p /app/output

ENV FLASK_ENV=production
ENV PORT=5050

EXPOSE $PORT

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5050/')"

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5050", "--workers", "2", "--timeout", "120"]
