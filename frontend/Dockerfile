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


#FROM nginxinc/nginx-unprivileged:stable-alpine
# FROM nginxinc/nginx-unprivileged:1.27-alpine
#COPY index.html /usr/share/nginx/html/index.html
#EXPOSE 8080

FROM nginxinc/nginx-unprivileged:stable-alpine

# Patch OS packages (fixes brotli, curl, expat, musl, zlib findings from Trivy)
USER root
RUN apk upgrade --no-cache
# Go back to the non-root user this image normally runs as
USER 101

COPY index.html /usr/share/nginx/html/index.html
EXPOSE 8080