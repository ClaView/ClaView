# Shared backend image: pipeline, module-a, module-b and module-c run this one image
# with different commands (docker-compose.yaml). Dependencies come from requirements.txt only.
FROM python:3.12.3-slim

WORKDIR /claview

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --upgrade pip
COPY requirements.txt /claview/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY common   /claview/common
COPY modules  /claview/modules
COPY pipeline /claview/pipeline
COPY data     /claview/data
COPY eval     /claview/eval

ENV TZ=Asia/Seoul \
    PROJECT_MAIN_DIRECTORY=/claview \
    PYTHONPATH=/claview/common:/claview/modules/a:/claview/modules/b:/claview/modules/c:/claview/pipeline \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANGCHAIN_TRACING_V2=false \
    LANGSMITH_TRACING=false

EXPOSE 8888
# Each service sets its own app in docker-compose.yaml (claview_a_api.api:app, ...).
CMD ["uvicorn", "claview_pipeline.api:app", "--host", "0.0.0.0", "--port", "8888", "--no-server-header"]
