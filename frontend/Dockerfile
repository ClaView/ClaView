# Screen image. Build context: repository root (the build reads eval/cases/golden for the example questions).
# Stage 1 patches the Highcharts screen draft into dist/index.html (frontend/build/ui_build.py).
# Stage 2 serves it with nginx and proxies /api/ to the pipeline (the only backend the screen calls).
FROM python:3.12.3-slim AS build
WORKDIR /build
COPY frontend/build  /build/frontend/build
COPY frontend/src    /build/frontend/src
COPY eval/cases/golden /build/eval/cases/golden
ENV CLAVIEW_ROOT=/build CLAVIEW_DIST=/build/dist PYTHONUTF8=1
RUN python -B frontend/build/ui_build.py

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/security_headers.conf /etc/nginx/snippets/security_headers.conf
COPY --from=build /build/dist /usr/share/nginx/html
EXPOSE 80
