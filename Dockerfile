# ── codex-card-app ──────────────────────────────────────────────────────────
# 名片识别与客户调研系统  |  端口 8000  |  Python 3.11 + FastAPI
# ---------------------------------------------------------------------------
FROM python:3.11-slim

WORKDIR /app

# 系统依赖（RapidOCR 需要 libgomp）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY app.py           .
COPY templates/       templates/
COPY config/          config/

# 运行时自动创建的目录（可选：让镜像预建好以避免权限问题）
RUN mkdir -p uploads reports/ocr reports/research reports/Unknown \
             database out

# 持久化卷：数据库、上传图片、报告、导出 CSV、API 配置
VOLUME ["/app/database", "/app/uploads", "/app/reports", "/app/out", "/app/config"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "app.py"]
