ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

# Install git and gh CLI (needed for integration tests)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY src/ src/
COPY tests/ tests/
COPY templates/ templates/

# Install dependencies
RUN uv venv /app/.venv && \
    uv pip install --python /app/.venv/bin/python ".[dev]"

ENV PATH="/app/.venv/bin:$PATH"

# Configure git for tests
RUN git config --global user.email "test@wade.dev" && \
    git config --global user.name "wade test" && \
    git config --global init.defaultBranch main

CMD ["pytest", "tests/", "-v", "--ignore=tests/live"]
