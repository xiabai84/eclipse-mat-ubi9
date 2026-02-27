# Dockerfile Multi-Stage Build with UBI9-minimal

**Date:** 2026-02-27
**Status:** Approved

## Goal

Complete the 3-stage multi-stage Dockerfile build and use Red Hat UBI9-minimal as the base image for all stages (including runtime).

## Current State

The Dockerfile has only Stage 1 (rpm-builder) implemented. Stages 2 (pip-builder) and 3 (runtime) are described in comments but missing entirely. The image is unrunnable.

## Design

### Stage 1 — `rpm-builder` (existing, minor cleanup)

- **Base:** `registry.access.redhat.com/ubi9/ubi9-minimal:latest`
- **Purpose:** Download Java 17 JRE RPMs, extract via `unpackRPM.sh`, download Eclipse MAT
- **Install:** `yum-utils`, `cpio`, `wget`, `unzip` (build-time only)
- **Outputs:** `/build/root` (Java 17 JRE files), `/opt/eclipse-mat`
- **Discarded after build**

### Stage 2 — `pip-builder` (new)

- **Base:** `registry.access.redhat.com/ubi9/ubi9-minimal:latest`
- **Purpose:** Build Python virtual environment with all production dependencies
- **Install:** `python3`, `python3-pip`, `python3-devel`, `gcc` (for lxml C compilation)
- **Actions:**
  - Create venv at `/opt/venv`
  - `pip install --no-cache-dir -r requirements.txt`
- **Output:** `/opt/venv` (self-contained Python venv)
- **Discarded after build**

### Stage 3 — `runtime` (new)

- **Base:** `registry.access.redhat.com/ubi9/ubi9-minimal:latest`
- **Purpose:** Minimal production runtime
- **Install via microdnf:** `python3`, `bash`, `unzip` only
- **COPY from rpm-builder:**
  - `/build/root/usr/lib/jvm` → `/usr/lib/jvm` (Java 17 JRE)
  - `/build/root/usr/share/javazi-1-8` → `/usr/share/javazi-1-8` (timezone data)
  - `/build/root/usr/lib64/lib*.so*` → `/usr/lib64/` (NSS/NSPR shared libs)
  - `/opt/eclipse-mat` → `/opt/eclipse-mat`
- **COPY from pip-builder:**
  - `/opt/venv` → `/opt/venv`
- **COPY from build context:**
  - `backend/` → `/opt/mat-service/`
  - `docker/scripts/*` → `/usr/local/bin/`
- **ENV:**
  - `JAVA_HOME=/usr/lib/jvm/jre-17-openjdk`
  - `PATH=/opt/venv/bin:$JAVA_HOME/bin:/usr/local/bin:$PATH`
  - `MAT_TIMEOUT=600`
- **User:** `mat` (UID 1001, non-root)
- **Volumes:** `/heapdumps`, `/reports`
- **EXPOSE:** 8080
- **ENTRYPOINT:** `["entrypoint.sh"]`
- **CMD:** `["service"]`

## Runtime Dependencies (final image only)

| Package | Why |
|---------|-----|
| `python3` | FastAPI service runtime |
| `bash` | entrypoint.sh and all shell scripts |
| `unzip` | report_script.sh extracts ZIP at runtime |

## Key Decisions

1. **JRE, not JDK** — Eclipse MAT only needs `java` on PATH. `java-17-openjdk-headless` (JRE) is sufficient.
2. **UBI9-minimal everywhere** — All 3 stages use the same base. Eliminates ABI mismatch for compiled C extensions (lxml).
3. **Non-root user** — Runtime runs as `mat` (UID 1001) for security.
4. **No pip in runtime** — pip and build tools stay in Stage 2.
5. **Selective COPY** — Only JVM, NSS libs, and timezone data copied from rpm-builder. No full `/build/root` to avoid overwriting runtime system files.

## Image Size Estimate

- UBI9-minimal base: ~100 MB
- Python 3 runtime: ~50 MB
- Java 17 JRE: ~80 MB
- Eclipse MAT: ~120 MB
- Python venv (FastAPI + deps): ~40 MB
- **Total: ~390 MB** (down from potential >900 MB with build tools included)
