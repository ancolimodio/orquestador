# Reference image for running Prompt Maestro gates inside ContainerSandbox.
#
#   docker build -t prompt-maestro-gates -f docker/gates.Dockerfile .
#
# Containers run without network access, so every tool and every dependency of the
# target repository must be installed at build time. Extend this image per project:
#
#   FROM prompt-maestro-gates
#   COPY requirements.txt /tmp/requirements.txt
#   RUN pip install -r /tmp/requirements.txt

FROM python:3.13-slim

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN pip install "ruff>=0.5" "mypy>=1.10" "pytest>=8.2" "bandit>=1.7"

# ContainerSandbox also passes --user; this default keeps manual runs unprivileged.
USER 65534:65534
WORKDIR /workspace
