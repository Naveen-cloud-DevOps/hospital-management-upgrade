FROM python:3.12-slim
ARG SERVICE
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common.py .
COPY ${SERVICE}/main.py ./main.py
RUN useradd -u 10001 app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
