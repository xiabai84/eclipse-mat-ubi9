# Dockerfile Multi-Stage Build — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete the 3-stage multi-stage Dockerfile on UBI9-minimal so the image is buildable and runnable.

**Architecture:** 3-stage build: rpm-builder (Java + MAT) → pip-builder (Python venv) → runtime (minimal). All stages use `registry.access.redhat.com/ubi9/ubi9-minimal:latest`. Only runtime artifacts are copied into the final image.

**Tech Stack:** Docker multi-stage, Red Hat UBI9-minimal, Python 3.9+, Java 17 JRE, Eclipse MAT 1.16.1

**Design doc:** `docs/plans/2026-02-27-dockerfile-multistage-ubi9-design.md`

---

### Task 1: Add Stage 2 — pip-builder

**Files:**
- Modify: `docker/Dockerfile` (append after line 99)

**Step 1: Add the pip-builder stage to the Dockerfile**

Append after the last line of Stage 1 (line 99):

```dockerfile

# =============================================================================
# Stage 2 — pip-builder
# =============================================================================
FROM registry.access.redhat.com/ubi9/ubi9-minimal:latest AS pip-builder

# Install Python + build tools needed to compile C extensions (lxml).
# These packages stay in this stage — only /opt/venv is carried forward.
RUN microdnf install -y --nodocs \
        python3 \
        python3-pip \
        python3-devel \
        gcc \
        libxml2-devel \
        libxslt-devel \
    && microdnf clean all \
    && rm -rf /var/cache/dnf

# Create a virtual environment and install production dependencies.
# --no-cache-dir keeps the layer small; --upgrade-pip avoids resolver warnings.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt
```

**Step 2: Verify syntax**

Run: `docker build --check -f docker/Dockerfile .` (or just ensure no syntax errors by running a dry build of stages 1-2)

---

### Task 2: Add Stage 3 — runtime

**Files:**
- Modify: `docker/Dockerfile` (append after Stage 2)

**Step 1: Add the runtime stage to the Dockerfile**

Append after Stage 2:

```dockerfile

# =============================================================================
# Stage 3 — runtime
# =============================================================================
FROM registry.access.redhat.com/ubi9/ubi9-minimal:latest AS runtime

LABEL maintainer="eclipse-mat-service" \
      description="Eclipse MAT heap analysis + FastAPI REST service" \
      org.opencontainers.image.source="https://github.com/your-org/eclipse-mat-service"

# ── Runtime packages only ────────────────────────────────────────────────────
# python3   → CPython interpreter (FastAPI service)
# bash      → entrypoint.sh and analysis shell scripts
# unzip     → report_script.sh extracts ZIP contents at runtime
RUN microdnf install -y --nodocs \
        python3 \
        bash \
        unzip \
    && microdnf clean all \
    && rm -rf /var/cache/dnf

# ── Java 17 JRE from rpm-builder ────────────────────────────────────────────
# Copy only the JVM tree and its runtime dependencies — not the full /build/root
# which would overwrite system files (glibc, etc.) in this image.
COPY --from=rpm-builder /build/root/usr/lib/jvm /usr/lib/jvm
COPY --from=rpm-builder /build/root/usr/share/javazi-1-8 /usr/share/javazi-1-8

# NSS / NSPR shared libraries — required for Java TLS (JSSE).
# Copy only the specific libs we need; wildcard keeps it forward-compatible.
COPY --from=rpm-builder /build/root/usr/lib64/libnspr4.so     /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libplds4.so     /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libplc4.so      /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libnssutil3.so  /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libsoftokn3.so  /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libfreebl3.so   /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libsctp.so.1    /usr/lib64/

# ── Eclipse MAT from rpm-builder ────────────────────────────────────────────
COPY --from=rpm-builder /opt/eclipse-mat /opt/eclipse-mat

# ── Python venv from pip-builder ────────────────────────────────────────────
COPY --from=pip-builder /opt/venv /opt/venv

# ── Application code & scripts ──────────────────────────────────────────────
COPY backend/ /opt/mat-service/
COPY docker/scripts/entrypoint.sh     /usr/local/bin/entrypoint.sh
COPY docker/scripts/analyze-heapdump.sh /usr/local/bin/analyze-heapdump.sh
COPY docker/scripts/oql-analyze.sh    /usr/local/bin/oql-analyze.sh
COPY docker/scripts/report_script.sh  /usr/local/bin/report_script.sh
RUN chmod +x /usr/local/bin/*.sh

# ── Environment ─────────────────────────────────────────────────────────────
ENV JAVA_HOME=/usr/lib/jvm/jre-17-openjdk \
    PATH="/opt/venv/bin:/usr/lib/jvm/jre-17-openjdk/bin:/usr/local/bin:${PATH}" \
    MAT_TIMEOUT=600 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ── Non-root user ───────────────────────────────────────────────────────────
RUN microdnf install -y --nodocs shadow-utils \
    && useradd --uid 1001 --no-create-home --shell /sbin/nologin mat \
    && microdnf remove -y shadow-utils \
    && microdnf clean all \
    && rm -rf /var/cache/dnf

RUN mkdir -p /heapdumps /reports \
    && chown mat:mat /heapdumps /reports

USER mat

EXPOSE 8080

VOLUME ["/heapdumps", "/reports"]

ENTRYPOINT ["entrypoint.sh"]
CMD ["service"]
```

---

### Task 3: Update header comments

**Files:**
- Modify: `docker/Dockerfile` (lines 10-16)

**Step 1: Update the Stage 2/3 comments to reflect UBI9-minimal**

Replace `(python-base-image)` references in the header:

```
# Stage 2  pip-builder   (ubi9-minimal)
#   Builds the Python venv inside the same UBI9-minimal base that runs in
#   production.  This guarantees that compiled C extensions (lxml, etc.) are
#   linked against the correct CPython ABI.
#
# Stage 3  runtime       (ubi9-minimal)
#   The final image.  Only runtime artefacts are copied in — no build tools,
#   no package manager cache, no download artefacts.
```

---

### Task 4: Build and verify the image

**Step 1: Build the image**

Run:
```bash
docker build --platform linux/amd64 -f docker/Dockerfile -t eclipse-mat .
```

Expected: Build completes with all 3 stages. Final image is tagged `eclipse-mat`.

**Step 2: Verify image size**

Run:
```bash
docker images eclipse-mat --format "{{.Repository}}:{{.Tag}} {{.Size}}"
```

Expected: Image size should be in the 350-500 MB range (no build tools).

**Step 3: Smoke test — health endpoint**

Run:
```bash
docker run -d --name mat-test --platform linux/amd64 -p 8080:8080 eclipse-mat
sleep 3
curl -s http://localhost:8080/health
docker rm -f mat-test
```

Expected: `{"status":"healthy"}` or similar health response.

**Step 4: Smoke test — java available**

Run:
```bash
docker run --rm --platform linux/amd64 eclipse-mat shell -c "java -version"
```

Expected: `openjdk version "17.0.x"` output.

**Step 5: Smoke test — help**

Run:
```bash
docker run --rm --platform linux/amd64 eclipse-mat --help
```

Expected: The entrypoint help text listing all commands.

**Step 6: Commit**

```bash
git add docker/Dockerfile
git commit -m "feat: complete 3-stage multi-stage Dockerfile on UBI9-minimal

- Add Stage 2 (pip-builder): builds Python venv with FastAPI deps on UBI9-minimal
- Add Stage 3 (runtime): minimal UBI9-minimal with python3, bash, unzip only
- Selective COPY of Java JRE, NSS libs, Eclipse MAT, and Python venv
- Non-root 'mat' user (UID 1001)
- No build tools, pip, or package manager cache in final image"
```

---

### Task 5: Update CLAUDE.md and README if needed

**Files:**
- Check: `CLAUDE.md`, `README.md`

**Step 1: Verify CLAUDE.md build commands still work**

The existing commands in CLAUDE.md should work unchanged:
```bash
docker build --platform linux/amd64 -f docker/Dockerfile -t eclipse-mat .
```

No update needed if this is still correct.

**Step 2: Verify README accuracy**

Check that README.md multi-stage build description matches the new 3-stage reality. Update if it references the old incomplete state.
