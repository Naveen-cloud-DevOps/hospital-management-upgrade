# #FROM nginxinc/nginx-unprivileged:stable-alpine
# # FROM nginxinc/nginx-unprivileged:1.27-alpine
# #COPY index.html /usr/share/nginx/html/index.html
# #EXPOSE 8080

# FROM nginxinc/nginx-unprivileged:stable-alpine

# # Patch OS packages (fixes brotli, curl, expat, musl, zlib findings from Trivy)
# USER root
# RUN apk upgrade --no-cache
# # Go back to the non-root user this image normally runs as
# USER 101

# COPY index.html /usr/share/nginx/html/index.html
# EXPOSE 8080
FROM python:3.12-slim
ARG SERVICE
WORKDIR /app
# Install the latest Debian security patches (fixes Trivy HIGH findings such as zlib1g)
RUN apt-get update \
    && apt-get install -y --only-upgrade --no-install-recommends zlib1g \
    && apt-get upgrade -y --no-install-recommends \
    && dpkg -s zlib1g | grep '^Version' \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common.py .
COPY ${SERVICE}/main.py ./main.py
RUN useradd -u 10001 app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]