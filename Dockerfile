FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY mediaseed ./mediaseed
RUN pip install --no-cache-dir . && useradd --uid 1000 --create-home mediaseed && mkdir /data && chown mediaseed:mediaseed /data
USER mediaseed
EXPOSE 18800
CMD ["uvicorn", "mediaseed.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "18800"]