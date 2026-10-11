FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --system --uid 10001 appuser && mkdir /data && chown appuser /data
COPY resip/ ./resip/
USER appuser
EXPOSE 8787
CMD ["uvicorn", "resip.api:make_app", "--factory", "--host", "0.0.0.0", "--port", "8787", "--workers", "1", "--no-access-log"]
