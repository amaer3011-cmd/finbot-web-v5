# نفس إصدار playwright في requirements.txt (1.48.0) — المتصفحات متسطبة جوه الصورة
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    BOT_DIR=/app/bot

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000
# Railway بيحقن PORT؛ process واحدة فقط (حالة الطابور في الذاكرة)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
