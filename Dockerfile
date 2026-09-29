FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STOCK_CHART_DATA_DIR=/app/charts

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 chart \
    && mkdir -p /app/charts \
    && chown chart:chart /app/charts

COPY ai_chat.py chart.py server.py template.html search.html ./

USER chart
EXPOSE 8765
CMD ["python", "server.py", "--host", "0.0.0.0", "--no-open"]
