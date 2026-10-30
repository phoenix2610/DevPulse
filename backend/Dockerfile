FROM docker:27.4.1-cli AS dockercli

FROM python:3.12

COPY certs/ /tmp/extra-certs/
RUN if ls /tmp/extra-certs/*.crt >/dev/null 2>&1; then cp /tmp/extra-certs/*.crt /usr/local/share/ca-certificates/ && update-ca-certificates; fi \
    && rm -rf /tmp/extra-certs
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt \
    PYTHONUNBUFFERED=1

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN useradd -m -u 1000 app
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m venv /opt/semgrep && /opt/semgrep/bin/pip install --no-cache-dir semgrep==1.99.0 "setuptools<81" \
    && ln -s /opt/semgrep/bin/semgrep /usr/local/bin/semgrep
COPY alembic.ini ./
COPY migrations ./migrations
COPY prompts ./prompts
COPY rules ./rules
COPY app ./app
RUN mkdir -p /data/repos && chown -R app /data
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
