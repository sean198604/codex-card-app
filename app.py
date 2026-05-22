from __future__ import annotations

import asyncio
import csv
import json
import os
import queue
import re
import shutil
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
VENV_SITE = BASE_DIR / ".venv" / "Lib" / "site-packages"
if VENV_SITE.exists() and str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))

import httpx

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

UPLOAD_DIR = BASE_DIR / "uploads"
REPORT_DIR = BASE_DIR / "reports"
RESEARCH_REPORT_DIR = REPORT_DIR / "research"
DB_DIR = BASE_DIR / "database"
DB_PATH = DB_DIR / "clients.db"
OUT_DIR = BASE_DIR / "out"
OUT_CSV_PATH = OUT_DIR / "clients_export.csv"
RESEARCH_OUT_CSV_PATH = OUT_DIR / "research_stats.csv"
CONFIG_DIR = BASE_DIR / "config"
API_CONFIG_PATH = CONFIG_DIR / "api_config.json"

for p in (UPLOAD_DIR, REPORT_DIR, RESEARCH_REPORT_DIR, DB_DIR, OUT_DIR, CONFIG_DIR):
    p.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Client Card MVP", version="0.5.0")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/config-static", StaticFiles(directory=str(CONFIG_DIR)), name="config_static")

_ocr_engine = None
_ocr_backend = None
OCR_THREADS = int(os.getenv("OCR_THREADS", "10"))
OCR_POOL = ThreadPoolExecutor(max_workers=max(1, OCR_THREADS))
RESEARCH_THREADS = 1
RESEARCH_QUEUE: "queue.Queue[tuple[int, str]]" = queue.Queue()
RESEARCH_WORKER: threading.Thread | None = None
RESEARCH_WORKER_LOCK = threading.Lock()
OUT_FILE_LOCK = threading.Lock()
RESEARCH_OUT_FILE_LOCK = threading.Lock()

EXPORT_HEADERS = [
    "created_at",
    "company",
    "contact_name",
    "email",
    "phone",
    "website",
    "country",
    "card_image",
    "report_file",
]

RESEARCH_EXPORT_HEADERS = [
    "created_at",
    "client_id",
    "company",
    "country",
    "company_size",
    "product_categories",
    "target_markets",
    "end_customers",
    "company_reputation_score",
    "purchasing_power_score",
    "communication_convenience_score",
    "market_coverage_score",
    "payment_risk_score",
    "research_status",
    "research_report_file",
    "sources",
]

DEFAULT_QWEN_MODEL = "qwen-max"
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
QWEN_TIMEOUT_SECONDS = int(os.getenv("QWEN_TIMEOUT_SECONDS", "300"))
QWEN_MAX_RETRIES = int(os.getenv("QWEN_MAX_RETRIES", "2"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "15"))


class ClientNameUpdate(BaseModel):
    company: str


class ManualResearchRequest(BaseModel):
    company: str


def load_api_config() -> dict[str, str]:
    file_cfg: dict[str, str] = {}
    if API_CONFIG_PATH.exists():
        try:
            # Use utf-8-sig so Windows BOM files can be parsed correctly.
            parsed = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(parsed, dict):
                file_cfg = parsed
        except Exception:
            file_cfg = {}

    # Environment variables override file values only when non-empty.
    env_api_key = os.getenv("QWEN_API_KEY")
    env_model = os.getenv("QWEN_MODEL")
    env_base_url = os.getenv("QWEN_BASE_URL")

    api_key = (
        env_api_key.strip()
        if env_api_key and env_api_key.strip()
        else str(file_cfg.get("qwen_api_key", "")).strip()
    )
    model = (
        env_model.strip()
        if env_model and env_model.strip()
        else str(file_cfg.get("qwen_model", DEFAULT_QWEN_MODEL)).strip()
    )
    base_url = (
        env_base_url.strip()
        if env_base_url and env_base_url.strip()
        else str(file_cfg.get("qwen_base_url", DEFAULT_QWEN_BASE_URL)).strip()
    )

    return {
        "qwen_api_key": api_key,
        "qwen_model": model or DEFAULT_QWEN_MODEL,
        "qwen_base_url": base_url or DEFAULT_QWEN_BASE_URL,
    }


def resolve_chat_completions_url(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return DEFAULT_QWEN_BASE_URL
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return url + "/chat/completions"
    return url + "/chat/completions"


def ensure_api_config_file() -> None:
    if API_CONFIG_PATH.exists():
        return
    API_CONFIG_PATH.write_text(
        json.dumps(
            {
                "qwen_api_key": "",
                "qwen_model": DEFAULT_QWEN_MODEL,
                "qwen_base_url": DEFAULT_QWEN_BASE_URL,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


ensure_api_config_file()
_api_cfg = load_api_config()
QWEN_API_KEY = _api_cfg["qwen_api_key"]
QWEN_MODEL = _api_cfg["qwen_model"]
QWEN_BASE_URL = _api_cfg["qwen_base_url"]
QWEN_CHAT_COMPLETIONS_URL = resolve_chat_completions_url(QWEN_BASE_URL)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                company TEXT,
                contact_name TEXT,
                email TEXT,
                phone TEXT,
                website TEXT,
                country TEXT,
                card_image TEXT,
                report_file TEXT,
                raw_text TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS researches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                company TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                country TEXT,
                company_size TEXT,
                product_categories TEXT,
                target_markets TEXT,
                end_customers TEXT,
                company_reputation_score REAL,
                purchasing_power_score REAL,
                communication_convenience_score REAL,
                market_coverage_score REAL,
                payment_risk_score REAL,
                research_report_file TEXT,
                sources_json TEXT,
                report_markdown TEXT,
                raw_response TEXT,
                error_message TEXT,
                UNIQUE(client_id)
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


init_db()

def insert_client(row: dict[str, str], raw_text: str) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO clients (
                created_at, company, contact_name, email, phone, website,
                country, card_image, report_file, raw_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("created_at", ""),
                row.get("company", ""),
                row.get("contact_name", ""),
                row.get("email", ""),
                row.get("phone", ""),
                row.get("website", ""),
                row.get("country", ""),
                row.get("card_image", ""),
                row.get("report_file", ""),
                raw_text,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def upsert_research_status(client_id: int, company: str, status: str, error_message: str = "") -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO researches (client_id, company, status, created_at, updated_at, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id)
            DO UPDATE SET company=excluded.company, status=excluded.status,
                          updated_at=excluded.updated_at, error_message=excluded.error_message
            """,
            (client_id, company, status, now, now, error_message),
        )
        conn.commit()
    finally:
        conn.close()


def save_research_result(client_id: int, company: str, data: dict[str, Any], raw_response: str) -> dict[str, str]:
    now = datetime.now().isoformat(timespec="seconds")

    country = str(data.get("country", "未找到"))
    company_size = str(data.get("company_size", "未找到"))
    product_categories = str(data.get("product_categories", "未找到"))
    target_markets = str(data.get("target_markets", "未找到"))
    end_customers = str(data.get("end_customers", "未找到"))
    company_reputation_score = float(data.get("company_reputation_score", 0) or 0)
    purchasing_power_score = float(data.get("purchasing_power_score", 0) or 0)
    communication_convenience_score = float(data.get("communication_convenience_score", 0) or 0)
    market_coverage_score = float(data.get("market_coverage_score", 0) or 0)
    payment_risk_score = float(data.get("payment_risk_score", 0) or 0)
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        sources = [str(sources)]

    report_text = str(data.get("report", "未找到"))

    safe_company = sanitize_name(company)
    report_name = f"{safe_company}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_research.md"
    report_path = RESEARCH_REPORT_DIR / report_name

    source_lines = "\n".join([f"- {x}" for x in sources]) if sources else "- 未找到"
    md = f"""# {company} 市场调研报告

## 结构化字段
- 国别：{country}
- 规模：{company_size}
- 产品品类：{product_categories}
- 目标市场：{target_markets}
- 终端客户：{end_customers}
- 公司信誉评分：{company_reputation_score}
- 采购能力评分：{purchasing_power_score}
- 沟通便利评分：{communication_convenience_score}
- 市场辐射评分：{market_coverage_score}
- 付款风险评分：{payment_risk_score}

## 调研正文
{report_text}

## 信息来源
{source_lines}
"""
    report_path.write_text(md, encoding="utf-8")
    rel_report = str(report_path.relative_to(BASE_DIR))

    conn = get_conn()
    try:
        conn.execute(
            """
            UPDATE researches
            SET status=?, updated_at=?, country=?, company_size=?, product_categories=?,
                target_markets=?, end_customers=?, company_reputation_score=?,
                purchasing_power_score=?, communication_convenience_score=?,
                market_coverage_score=?, payment_risk_score=?, research_report_file=?,
                sources_json=?, report_markdown=?, raw_response=?, error_message=''
            WHERE client_id=?
            """,
            (
                "completed",
                now,
                country,
                company_size,
                product_categories,
                target_markets,
                end_customers,
                company_reputation_score,
                purchasing_power_score,
                communication_convenience_score,
                market_coverage_score,
                payment_risk_score,
                rel_report,
                json.dumps(sources, ensure_ascii=False),
                md,
                raw_response,
                client_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "created_at": now,
        "client_id": str(client_id),
        "company": company,
        "country": country,
        "company_size": company_size,
        "product_categories": product_categories,
        "target_markets": target_markets,
        "end_customers": end_customers,
        "company_reputation_score": str(company_reputation_score),
        "purchasing_power_score": str(purchasing_power_score),
        "communication_convenience_score": str(communication_convenience_score),
        "market_coverage_score": str(market_coverage_score),
        "payment_risk_score": str(payment_risk_score),
        "research_status": "completed",
        "research_report_file": rel_report,
        "sources": " | ".join(sources),
    }


def list_clients(limit: int = 50) -> list[dict[str, str]]:
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            SELECT created_at, company, contact_name, email, phone, website,
                   country, card_image, report_file
            FROM clients
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_client(client_id: int) -> dict[str, str] | None:
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            SELECT id, created_at, company, contact_name, email, phone, website,
                   country, card_image, report_file
            FROM clients
            WHERE id = ?
            LIMIT 1
            """,
            (client_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()


def update_client_company(client_id: int, company: str) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE clients SET company = ? WHERE id = ?",
            (company, client_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_research_status(client_id: int) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            SELECT client_id, company, status, updated_at, error_message
            FROM researches
            WHERE client_id = ?
            LIMIT 1
            """,
            (client_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_researches(limit: int = 50) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            SELECT client_id, company, status, updated_at, country, company_size,
                   product_categories, target_markets, end_customers,
                   company_reputation_score, purchasing_power_score,
                   communication_convenience_score, market_coverage_score,
                   payment_risk_score, research_report_file, error_message
            FROM researches
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_research_markdown(client_id: int) -> dict[str, str] | None:
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            SELECT client_id, company, status, research_report_file, report_markdown
            FROM researches
            WHERE client_id = ?
            LIMIT 1
            """,
            (client_id,),
        )
        row = cur.fetchone()
        if not row:
            return None

        md = str(row["report_markdown"] or "")
        report_file = str(row["research_report_file"] or "")
        if not md and report_file:
            p = BASE_DIR / report_file
            if p.exists():
                md = p.read_text(encoding="utf-8", errors="ignore")

        return {
            "client_id": str(row["client_id"] or ""),
            "company": str(row["company"] or ""),
            "status": str(row["status"] or ""),
            "research_report_file": report_file,
            "markdown": md,
        }
    finally:
        conn.close()


def count_clients() -> int:
    conn = get_conn()
    try:
        cur = conn.execute("SELECT COUNT(1) FROM clients")
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def append_to_out_csv(row: dict[str, str]) -> None:
    with OUT_FILE_LOCK:
        file_exists = OUT_CSV_PATH.exists()
        with OUT_CSV_PATH.open("a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=EXPORT_HEADERS)
            if not file_exists:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in EXPORT_HEADERS})


def fetch_clients_export_rows() -> list[dict[str, str]]:
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            SELECT created_at, company, contact_name, email, phone, website,
                   country, card_image, report_file
            FROM clients
            ORDER BY id ASC
            """
        )
        return [
            {
                "created_at": str(r["created_at"] or ""),
                "company": str(r["company"] or ""),
                "contact_name": str(r["contact_name"] or ""),
                "email": str(r["email"] or ""),
                "phone": str(r["phone"] or ""),
                "website": str(r["website"] or ""),
                "country": str(r["country"] or ""),
                "card_image": str(r["card_image"] or ""),
                "report_file": str(r["report_file"] or ""),
            }
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def rebuild_clients_export_csv() -> int:
    rows = fetch_clients_export_rows()
    with OUT_FILE_LOCK:
        with OUT_CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=EXPORT_HEADERS)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in EXPORT_HEADERS})
    return len(rows)


def fetch_research_export_rows() -> list[dict[str, str]]:
    conn = get_conn()
    try:
        cur = conn.execute(
            """
            SELECT
                updated_at AS created_at,
                client_id,
                company,
                COALESCE(country, '') AS country,
                COALESCE(company_size, '') AS company_size,
                COALESCE(product_categories, '') AS product_categories,
                COALESCE(target_markets, '') AS target_markets,
                COALESCE(end_customers, '') AS end_customers,
                COALESCE(company_reputation_score, '') AS company_reputation_score,
                COALESCE(purchasing_power_score, '') AS purchasing_power_score,
                COALESCE(communication_convenience_score, '') AS communication_convenience_score,
                COALESCE(market_coverage_score, '') AS market_coverage_score,
                COALESCE(payment_risk_score, '') AS payment_risk_score,
                status AS research_status,
                COALESCE(research_report_file, '') AS research_report_file,
                COALESCE(sources_json, '[]') AS sources_json
            FROM researches
            WHERE status = 'completed'
            ORDER BY id ASC
            """
        )
        rows: list[dict[str, str]] = []
        for r in cur.fetchall():
            sources_raw = str(r["sources_json"] or "[]")
            try:
                sources_val = json.loads(sources_raw)
                if isinstance(sources_val, list):
                    sources = " | ".join([str(x) for x in sources_val])
                else:
                    sources = str(sources_val)
            except Exception:
                sources = sources_raw

            rows.append(
                {
                    "created_at": str(r["created_at"] or ""),
                    "client_id": str(r["client_id"] or ""),
                    "company": str(r["company"] or ""),
                    "country": str(r["country"] or ""),
                    "company_size": str(r["company_size"] or ""),
                    "product_categories": str(r["product_categories"] or ""),
                    "target_markets": str(r["target_markets"] or ""),
                    "end_customers": str(r["end_customers"] or ""),
                    "company_reputation_score": str(r["company_reputation_score"] or ""),
                    "purchasing_power_score": str(r["purchasing_power_score"] or ""),
                    "communication_convenience_score": str(r["communication_convenience_score"] or ""),
                    "market_coverage_score": str(r["market_coverage_score"] or ""),
                    "payment_risk_score": str(r["payment_risk_score"] or ""),
                    "research_status": str(r["research_status"] or ""),
                    "research_report_file": str(r["research_report_file"] or ""),
                    "sources": sources,
                }
            )
        return rows
    finally:
        conn.close()


def rebuild_research_export_csv() -> int:
    rows = fetch_research_export_rows()
    with RESEARCH_OUT_FILE_LOCK:
        with RESEARCH_OUT_CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=RESEARCH_EXPORT_HEADERS)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in RESEARCH_EXPORT_HEADERS})
    return len(rows)


def get_ocr_engine():
    global _ocr_engine, _ocr_backend
    if _ocr_engine is not None:
        return _ocr_engine, _ocr_backend

    try:
        from rapidocr_onnxruntime import RapidOCR

        _ocr_engine = RapidOCR()
        _ocr_backend = "rapidocr"
        return _ocr_engine, _ocr_backend
    except Exception:
        pass

    try:
        from paddleocr import PaddleOCR

        _ocr_engine = PaddleOCR(lang="en")
        _ocr_backend = "paddleocr"
        return _ocr_engine, _ocr_backend
    except Exception as exc:
        raise RuntimeError(
            "No OCR backend available. Please install dependencies: pip install -r requirements.txt"
        ) from exc


def flatten_ocr_result(result: Any) -> str:
    lines: list[str] = []
    for block in result or []:
        for item in block or []:
            try:
                lines.append(str(item[1][0]).strip())
            except Exception:
                continue
    return "\n".join([x for x in lines if x])


def flatten_rapidocr_result(result: Any) -> str:
    lines: list[str] = []
    for item in result or []:
        try:
            lines.append(str(item[1]).strip())
        except Exception:
            continue
    return "\n".join([x for x in lines if x])


def run_ocr(save_path: Path) -> str:
    ocr, backend = get_ocr_engine()
    if backend == "rapidocr":
        result, _ = ocr(str(save_path))
        return flatten_rapidocr_result(result)
    result = ocr.predict(str(save_path))
    return flatten_ocr_result(result)


def parse_fields(raw_text: str) -> dict[str, str]:
    text = raw_text.strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    phone_match = re.search(r"(?:\+?\d[\d\-\s()]{7,}\d)", text)
    website_match = re.search(r"(?:https?://)?(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", text)

    company = lines[0] if lines else "Unknown Company"
    name = lines[1] if len(lines) > 1 else ""

    return {
        "company": company,
        "contact_name": name,
        "email": email_match.group(0) if email_match else "",
        "phone": phone_match.group(0) if phone_match else "",
        "website": website_match.group(0) if website_match else "",
    }


def sanitize_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return value[:80] or "unknown"


def build_report(fields: dict[str, str], raw_text: str) -> str:
    bullets = []
    if fields["website"]:
        bullets.append(f"- 官网：{fields['website']}")
    if fields["email"]:
        bullets.append(f"- 邮箱：{fields['email']}")
    if fields["phone"]:
        bullets.append(f"- 电话：{fields['phone']}")
    if not bullets:
        bullets.append("- 未识别到标准联系方式，请人工核对")

    return f"""# {fields['company']} 客户简报

- 联系人：{fields['contact_name'] or '未知'}

## 关键字段
{os.linesep.join(bullets)}

## OCR 原文
```
{raw_text or '（空）'}
```
"""


def build_research_prompt(company_name: str) -> str:
    return f"""Research the following company using publicly available and reliable sources (official registries, company websites, industry databases, LinkedIn, B2B platforms, news, etc.). Do NOT fabricate information; write "not found" if unavailable.

Company name: {company_name}

Output ONLY a valid JSON object — no markdown, no extra text, no explanation. Use exactly these fields:
{{
  "country": "country of the company, or 'not found'",
  "company_size": "description based on employee count / revenue / area, or 'not found'",
  "product_categories": "main product categories",
  "target_markets": "target markets",
  "end_customers": "main end customers",
  "company_reputation_score": <integer 1-10>,
  "purchasing_power_score": <integer 1-10>,
  "communication_convenience_score": <integer 1-10>,
  "market_coverage_score": <integer 1-10>,
  "payment_risk_score": <integer 1-10>,
  "report": "full research report covering: 1.Company Overview 2.Products & Markets 3.Reputation & Customers 4.Financial Strength 5.Risk Assessment",
  "sources": ["source_url_1", "source_url_2"]
}}

Scoring rules: higher score = stronger (payment_risk_score: higher = riskier). If data is insufficient, give a conservative score and explain in the report."""


def call_qwen(company_name: str) -> str:
    if not QWEN_API_KEY:
        raise RuntimeError("QWEN_API_KEY not set")

    payload = {
        "model": QWEN_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a professional foreign trade customer research analyst. Always respond with valid JSON only — no markdown fences, no extra text, no explanation outside the JSON.",
            },
            {
                "role": "user",
                "content": build_research_prompt(company_name),
            },
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json",
    }

    last_err: Exception | None = None
    for attempt in range(1, max(1, QWEN_MAX_RETRIES) + 1):
        try:
            with httpx.Client(timeout=QWEN_TIMEOUT_SECONDS) as client:
                resp = client.post(
                    QWEN_CHAT_COMPLETIONS_URL,
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            last_err = exc
            if attempt < max(1, QWEN_MAX_RETRIES):
                # simple backoff for transient network issues
                time.sleep(min(3 * attempt, 8))
            else:
                raise RuntimeError(f"Qwen request failed after {attempt} attempt(s): {exc}") from exc

    raise RuntimeError(f"Qwen request failed: {last_err}")


def parse_model_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        if text.endswith("```"):
            text = text[:-3].strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Model output is not valid JSON")


def run_research_pipeline(client_id: int, company_name: str) -> None:
    if not company_name:
        upsert_research_status(client_id, company_name or "Unknown Company", "failed", "empty company name")
        return

    upsert_research_status(client_id, company_name, "running", "")

    try:
        raw = call_qwen(company_name)
        parsed = parse_model_json(raw)
        save_research_result(client_id, company_name, parsed, raw)
        rebuild_research_export_csv()
    except Exception as exc:
        upsert_research_status(client_id, company_name, "failed", str(exc)[:500])


def research_worker_loop() -> None:
    while True:
        client_id, company_name = RESEARCH_QUEUE.get()
        try:
            run_research_pipeline(client_id, company_name)
        finally:
            RESEARCH_QUEUE.task_done()


def ensure_research_worker_started() -> None:
    global RESEARCH_WORKER
    with RESEARCH_WORKER_LOCK:
        if RESEARCH_WORKER and RESEARCH_WORKER.is_alive():
            return
        RESEARCH_WORKER = threading.Thread(target=research_worker_loop, daemon=True)
        RESEARCH_WORKER.start()


def enqueue_research(client_id: int, company_name: str) -> str:
    if not QWEN_API_KEY:
        upsert_research_status(client_id, company_name, "skipped_no_api_key", "QWEN_API_KEY not set")
        return "skipped_no_api_key"

    existing = get_research_status(client_id)
    if existing and existing.get("status") in {"queued", "running"}:
        return str(existing.get("status"))

    ensure_research_worker_started()
    upsert_research_status(client_id, company_name, "queued", "")
    RESEARCH_QUEUE.put((client_id, company_name))
    return "queued"


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    rebuild_clients_export_csv()
    rebuild_research_export_csv()
    ensure_research_worker_started()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})


@app.get("/health")
def health():
    return {
        "status": "ok",
        "time": datetime.now().isoformat(timespec="seconds"),
        "db": str(DB_PATH),
        "out_file": str(OUT_CSV_PATH),
        "research_out_file": str(RESEARCH_OUT_CSV_PATH),
        "api_config_file": str(API_CONFIG_PATH),
        "ocr_threads": OCR_THREADS,
        "research_threads": RESEARCH_THREADS,
        "research_queue_size": RESEARCH_QUEUE.qsize(),
        "qwen_configured": bool(QWEN_API_KEY),
        "qwen_model": QWEN_MODEL,
        "qwen_base_url": QWEN_BASE_URL,
        "qwen_chat_completions_url": QWEN_CHAT_COMPLETIONS_URL,
        "qwen_timeout_seconds": QWEN_TIMEOUT_SECONDS,
        "qwen_max_retries": QWEN_MAX_RETRIES,
        "max_upload_mb": MAX_UPLOAD_MB,
    }


@app.get("/clients")
def clients(limit: int = 50):
    limit = max(1, min(500, int(limit)))
    items = list_clients(limit=limit)
    return {"count": count_clients(), "items": items}


@app.patch("/clients/{client_id}")
def update_client(client_id: int, payload: ClientNameUpdate):
    company = (payload.company or "").strip()
    if not company:
        raise HTTPException(status_code=400, detail="Company name cannot be empty")
    ok = update_client_company(client_id, company)
    if not ok:
        raise HTTPException(status_code=404, detail="Client not found")
    rebuild_clients_export_csv()
    return {"ok": True, "client_id": client_id, "company": company}


@app.post("/clients/{client_id}/research")
def start_client_research(client_id: int):
    client = get_client(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    company = str(client.get("company") or "").strip()
    if not company:
        raise HTTPException(status_code=400, detail="Client company is empty")
    status = enqueue_research(client_id, company)
    return {"ok": True, "client_id": client_id, "company": company, "research_status": status}


@app.post("/research/manual")
def start_manual_research(payload: ManualResearchRequest):
    company = (payload.company or "").strip()
    if not company:
        raise HTTPException(status_code=400, detail="Company name cannot be empty")

    row = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "company": company,
        "contact_name": "",
        "email": "",
        "phone": "",
        "website": "",
        "country": "",
        "card_image": "",
        "report_file": "",
    }
    client_id = insert_client(row, raw_text="")
    append_to_out_csv(row)
    status = enqueue_research(client_id, company)
    return {"ok": True, "client_id": client_id, "company": company, "research_status": status}


@app.get("/researches")
def researches(limit: int = 50):
    limit = max(1, min(500, int(limit)))
    items = list_researches(limit=limit)
    return {"count": len(items), "items": items}


@app.get("/researches/{client_id}/markdown")
def research_markdown(client_id: int):
    row = get_research_markdown(client_id)
    if not row:
        raise HTTPException(status_code=404, detail="Research not found")
    return {"ok": True, **row}


@app.post("/export/researches")
def export_researches():
    count = rebuild_research_export_csv()
    return {
        "ok": True,
        "rows": count,
        "research_out_file": str(RESEARCH_OUT_CSV_PATH),
    }


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    # protect server stability in multi-user usage
    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)
    if file_size > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"File too large. Limit is {MAX_UPLOAD_MB}MB")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = sanitize_name(Path(file.filename).stem)
    save_name = f"{safe_name}_{ts}{suffix}"
    save_path = UPLOAD_DIR / save_name

    with save_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        loop = asyncio.get_running_loop()
        raw_text = await loop.run_in_executor(OCR_POOL, run_ocr, save_path)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": "OCR failed",
                "message": str(exc),
                "uploaded_file": str(save_path.name),
            },
        )

    fields = parse_fields(raw_text)
    report_content = build_report(fields, raw_text)

    country_dir = REPORT_DIR / "ocr"
    country_dir.mkdir(parents=True, exist_ok=True)
    report_file = country_dir / f"{sanitize_name(fields['company'])}_{ts}.md"
    report_file.write_text(report_content, encoding="utf-8")

    row = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        **fields,
        "country": "",
        "card_image": save_name,
        "report_file": str(report_file.relative_to(BASE_DIR)),
    }
    client_id = insert_client(row, raw_text)
    append_to_out_csv(row)

    return {
        "ok": True,
        "client_id": client_id,
        "fields": fields,
        "report_file": row["report_file"],
        "card_image": row["card_image"],
        "db_file": str(DB_PATH),
        "out_file": str(OUT_CSV_PATH),
        "research_status": "not_requested",
        "research_out_file": str(RESEARCH_OUT_CSV_PATH),
        "research_note": "manual start required after confirming company name",
        "research_poll_api": "/researches?limit=50",
    }


# ── ADMIN PATCH ──────────────────────────────────────────────────────────────
import io
from fastapi import Form
from fastapi.responses import StreamingResponse

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "1234")


def _check_admin(password: str):
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="密码错误")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


@app.post("/api/admin/verify")
def admin_verify(payload: dict = None):
    if payload is None:
        raise HTTPException(status_code=400, detail="Missing body")
    _check_admin(payload.get("password", ""))
    return {"ok": True}


@app.post("/api/admin/export")
def admin_export(payload: dict = None):
    """Batch export selected client_ids as CSV."""
    if payload is None:
        raise HTTPException(status_code=400, detail="Missing body")
    _check_admin(payload.get("password", ""))

    ids = payload.get("ids")   # list[int] | None  -> None means export all
    limit = max(1, min(1000, int(payload.get("limit", 500))))
    items = list_researches(limit=limit)

    if ids is not None:
        id_set = set(int(x) for x in ids)
        items = [r for r in items if int(r.get("client_id", 0)) in id_set]

    headers_row = RESEARCH_EXPORT_HEADERS
    output = io.StringIO()
    import csv as _csv
    writer = _csv.DictWriter(output, fieldnames=headers_row, extrasaction="ignore")
    writer.writeheader()
    for row in items:
        writer.writerow({k: (row.get(k) or "") for k in headers_row})

    output.seek(0)
    filename = f"research_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter(["\ufeff" + output.read()]),   # UTF-8 BOM for Excel
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/api/admin/delete/{client_id}")
async def admin_delete_record(client_id: int, request: Request):
    """Delete a single research record (admin only)."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    _check_admin(payload.get("password", ""))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM clients WHERE id = ?", (client_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="记录不存在")
    cur.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "deleted": client_id}
# ── END ADMIN PATCH ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)


