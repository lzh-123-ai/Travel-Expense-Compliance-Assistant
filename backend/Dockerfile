FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# 默认镜像保持轻量；显式构建开关才安装体积较大的 OCR 运行时。
ARG INSTALL_OCR=false
RUN if [ "$INSTALL_OCR" = "true" ]; then \
      pip install --no-cache-dir ".[ocr]"; \
    else \
      pip install --no-cache-dir .; \
    fi

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
