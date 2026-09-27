FROM python:3.10-slim

# Устанавливаем системные зависимости для Chromium
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем Chromium и его зависимости
RUN playwright install chromium
RUN playwright install-deps chromium

# Копируем проект
COPY . .

# Запуск с динамическим портом от Render ($PORT или 10000 по умолчанию)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
