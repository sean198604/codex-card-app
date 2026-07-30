# Codex Card — 名片识别与客户调研

智能名片识别系统，OCR + AI 自动完成客户背景调研，生成结构化客户档案。

## 技术栈

- **后端**: Python FastAPI
- **OCR**: 集成 OCR 引擎
- **AI 调研**: 大模型自动生成客户背景报告
- **部署**: Docker
- **端口**: 8000

## 功能

- 📸 **名片 OCR 识别** — 上传名片图片，提取姓名、公司、职位、联系方式
- 🔍 **客户背景调研** — AI 自动搜索并生成客户公司背景报告
- 📊 **客户档案管理** — SQLite 本地存储，支持 CSV 导出
- ⚡ **并发处理** — 线程池支持多任务并行 OCR
- 🎨 **EGO 品牌 UI** — 深蓝渐变主题

## 目录结构

```
├── app.py                    # FastAPI 主应用
├── templates/
│   ├── upload.html           # 名片上传页
│   └── admin.html            # 管理后台
├── config/
│   └── api_config.json       # API 密钥配置（不入库）
├── database/                 # SQLite 数据库
├── uploads/                  # 上传的名片图片
├── reports/                  # 生成的调研报告
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 快速启动

```bash
docker-compose up -d
```

访问 `http://192.168.1.246:8000`

## 配置

需要配置 `config/api_config.json` 中的大模型 API 密钥。参考 `config/api_config.example.json`。
