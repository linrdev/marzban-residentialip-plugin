FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && groupadd --gid 10001 appuser && useradd --system --uid 10001 --gid 10001 appuser && mkdir /data && chown appuser:appuser /data
COPY resip/ ./resip/
USER 10001:10001
EXPOSE 8787
CMD ["uvicorn", "resip.api:make_app", "--factory", "--host", "0.0.0.0", "--port", "8787", "--workers", "1", "--no-access-log"]
