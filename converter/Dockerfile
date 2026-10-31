FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac
ARG NVENCC_VERSION=9.39
ARG NVENCC_SHA256=7b1d716ac696d420846a6422613ccab51ab836fa695dc6a62ac17854149f203a
ENV DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl python3 python3-venv ffmpeg mkvtoolnix \
 && curl -fL --retry 3 "https://github.com/rigaya/NVEnc/releases/download/${NVENCC_VERSION}/nvencc_${NVENCC_VERSION}_amd64.deb" -o /tmp/nvencc.deb \
 && echo "${NVENCC_SHA256}  /tmp/nvencc.deb" | sha256sum -c - \
 && apt-get install -y --no-install-recommends /tmp/nvencc.deb \
 && python3 -m venv /opt/venv && rm -rf /var/lib/apt/lists/* /tmp/nvencc.deb
ENV PATH="/opt/venv/bin:${PATH}"
WORKDIR /app
COPY pyproject.toml ./
COPY mediaseed ./mediaseed
RUN pip install --no-cache-dir . && chmod -R a+rX /app
USER 1000:1000
CMD ["python", "-m", "mediaseed.conversion_worker"]
