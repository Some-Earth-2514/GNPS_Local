# ── Stage 1: build ──────────────────────────────────────────────────────────
# Install C++ build tools and compile any native wheels.
# These tools are NOT copied into the final image, keeping it small.
FROM python:3.10-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy both requirements files before installing.
# Copying them separately preserves Docker's layer cache:
# only the changed file invalidates its install layer.
COPY requirements.txt ./requirements_root.txt
COPY local_runner/requirements.txt ./requirements_local_runner.txt

# Install root deps first, then local_runner deps.
# --prefix=/install writes everything to a staging directory that gets
# copied into the lean runtime image (keeps build tools out of final image).
RUN pip install --upgrade pip setuptools wheel \
 && pip install --prefix=/install --no-cache-dir setuptools wheel \
 && pip install --prefix=/install --no-cache-dir -r requirements_root.txt \
 && pip install --prefix=/install --no-cache-dir -r requirements_local_runner.txt


# ── Stage 2: runtime ─────────────────────────────────────────────────────────
# Lean image: only the runtime library (libgomp1) and the installed packages.
FROM python:3.10-slim AS runtime

# libgomp1  — parallel processing in the analysis tools
# gcc       — required at runtime for psutil's compiled C extension
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from the builder stage.
COPY --from=builder /install /usr/local

# Copy the application source code.
WORKDIR /app
COPY . .

# Jobs are written here inside the container; this path is mounted as a
# Docker volume so data survives container restarts (see docker-compose.yml).
RUN mkdir -p /data/jobs /data/libraries

# Expose the web UI port.
EXPOSE 8000

# Run as a non-root user for security.
RUN useradd -m gnpsuser && chown -R gnpsuser /app /data
USER gnpsuser

# Start the server. Docker Compose overrides the port via the PORT env var
# if needed (defaults to 8000).
CMD cd /app/local_runner && python -u -m uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} 2>&1 | sed --unbuffered 's|0\.0\.0\.0|localhost|g'