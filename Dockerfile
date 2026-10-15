# Produce a stable dependency manifest: app code and release versions are not inputs.
FROM python:3.12-slim AS dependency-manifest
WORKDIR /manifest
COPY pyproject.toml ./
RUN python -c "import pathlib,tomllib; p=tomllib.loads(pathlib.Path('pyproject.toml').read_text()); r=sorted(set(p['project']['dependencies']+p['build-system']['requires'])); pathlib.Path('requirements.txt').write_text(chr(10).join(r)+chr(10))"

FROM python:3.12-slim
WORKDIR /app
COPY --from=dependency-manifest /manifest/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
RUN useradd --uid 1000 --create-home mediaseed && mkdir /data /downloads && chown mediaseed:mediaseed /data /downloads
COPY pyproject.toml ./
COPY mediaseed ./mediaseed
RUN chmod -R a+rX /app/mediaseed && pip install --no-cache-dir --no-deps --no-build-isolation --no-index .
USER mediaseed
EXPOSE 18800
CMD ["uvicorn", "mediaseed.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "18800"]
