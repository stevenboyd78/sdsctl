# Build this monorepo image from the repository root:
# docker build -f home-assistant/sds200/Dockerfile .
FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913 AS build

WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip wheel \
    --disable-pip-version-check \
    --wheel-dir /wheels \
    ".[web,mqtt]"

FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

ARG BUILD_VERSION=dev
ARG BUILD_ARCH=unknown

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.name="sds200" \
    io.hass.description="Uniden SDS200 scanner daemon and web dashboard for Home Assistant" \
    io.hass.url="https://github.com/stevenboyd78/sdsctl" \
    org.opencontainers.image.title="sds200 Home Assistant App" \
    org.opencontainers.image.description="Uniden SDS200 scanner daemon and web dashboard for Home Assistant" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.source="https://github.com/stevenboyd78/sdsctl"

ENV \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=build /wheels /wheels

RUN apt-get update \
    && apt-get install --yes --no-install-recommends lame openssl \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install \
        --disable-pip-version-check \
        --no-cache-dir \
        --no-index \
        --find-links=/wheels \
        "sds200[web,mqtt]" \
    && rm -rf /wheels

CMD ["python", "-m", "sds200.home_assistant_app_supervisor"]
