# Eclipse MAT Service

A Docker-based toolkit that pairs **Eclipse Memory Analyzer Tool (MAT)** with a
Python REST service to automate Java heap dump (`.hprof`) analysis and deliver
structured, actionable diagnostics.

---

## Table of Contents

1. [Quickstart](#quickstart)
2. [Architecture](#architecture)
3. [Docker Image](#docker-image)
4. [API Reference](#api-reference)
5. [Generate Heap Dumps](#generate-heap-dumps)
6. [Batch Analysis](#batch-analysis)
7. [Configuration](#configuration)
8. [Running Tests](#running-tests)
9. [Project Structure](#project-structure)
10. [Troubleshooting](#troubleshooting)

---

## Quickstart

> **Prerequisites:** Docker installed and running.
> Apple Silicon users **must** add `--platform linux/amd64` to every `docker` command (MAT is x86-only).

### 1. Build the image

```bash
git clone <repo-url> eclipse-mat-service
cd eclipse-mat-service

# Linux / x86
docker build -f docker/Dockerfile -t eclipse-mat .

# Apple Silicon / ARM
docker build --platform linux/amd64 -f docker/Dockerfile -t eclipse-mat .
```

### 2. Start the service

```bash
docker run -d \
  --name mat-service \
  --platform linux/amd64 \
  -p 8080:8080 \
  -v $(pwd)/heapdumps:/heapdumps \
  -v $(pwd)/reports:/reports \
  eclipse-mat
```

### 3. Verify

```bash
curl http://localhost:8080/health
```

```json
{
  "status": "ok",
  "service": "mat-analysis",
  "version": "3.1.0",
  "mat_available": true,
  "disk": {
    "reports": { "free_gb": 42.5, "total_gb": 100.0 },
    "heapdumps": { "free_gb": 42.5, "total_gb": 100.0 }
  }
}
```

Browse the interactive API docs at **http://localhost:8080/docs**.

### 4. Analyse a heap dump

```bash
# Human-readable report
curl -s -X POST http://localhost:8080/analyze/heapdump/report \
     -F "file=@./heapdumps/myapp.hprof"

# Structured JSON response
curl -s -X POST http://localhost:8080/analyze/heapdump \
     -F "file=@./heapdumps/myapp.hprof" | python3 -m json.tool
```

### 5. (Optional) Generate demo heap dumps

Requires Java 11+ on your host machine:

```bash
chmod +x demo/run-demo.sh
./demo/run-demo.sh all      # All 7 memory leak scenarios
./demo/run-demo.sh 3        # Single scenario
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Docker Container (eclipse-mat)                                  │
│                                                                  │
│  ┌─────────────────────┐    ┌──────────────────────────────┐     │
│  │  Eclipse MAT        │    │  Python REST Service         │     │
│  │  ParseHeapDump.sh   │    │  FastAPI  :8080              │     │
│  │                     │    │                              │     │
│  │  .hprof  ──────────►│    │  POST /analyze/heapdump      │     │
│  │          ┌──────────┘    │    • saves upload            │     │
│  │          │ ZIP reports   │    • runs MAT (subprocess)   │     │
│  │          ▼               │    • parses with BS4/lxml    │     │
│  │  Leak_Suspects.zip       │    • returns JSON / text     │     │
│  │  System_Overview.zip     │                              │     │
│  │  Top_Components.zip  ───►│  POST /analyze/all           │     │
│  └─────────────────────┘    │    • scans reports dir       │     │
│                             │    • runs all analyzers      │     │
│  ┌──────────────────────┐   └──────────────────────────────┘     │
│  │  Python Analyzers    │                                        │
│  │  suspects.py         │   Volume mounts                        │
│  │  overview.py         │   /heapdumps  ← .hprof uploads         │
│  │  top_components.py   │   /reports    ← ZIP report output      │
│  └──────────────────────┘                                        │
└──────────────────────────────────────────────────────────────────┘
```

**Data flow:**
```
Upload .hprof → Save to /heapdumps → Run MAT (subprocess) → Generate ZIPs in /reports
  → Parse ZIPs with BeautifulSoup → Extract tables/data → Build report → Return JSON or text
```

Three analysis workflows are supported:

| Workflow | Endpoint | Response |
|----------|----------|----------|
| Upload & analyse (human-readable) | `POST /analyze/heapdump/report` | `text/plain` |
| Upload & analyse (machine-readable) | `POST /analyze/heapdump` | JSON |
| Analyse pre-generated ZIP reports | `POST /analyze/all` | JSON |

---

## Docker Image

### Multi-Stage Build

The image uses a 3-stage multi-stage build. All stages are based on
[Red Hat UBI 9 Minimal](https://catalog.redhat.com/software/containers/ubi9/ubi-minimal/615bd9b4075b022acc111bf5)
(`registry.access.redhat.com/ubi9/ubi-minimal`).

| Stage | Purpose | Discarded |
|-------|---------|-----------|
| **`rpm-builder`** | Downloads Java 17 JRE RPMs, extracts them via `rpm2cpio`, downloads Eclipse MAT | Yes |
| **`pip-builder`** | Installs Python build tools, compiles a virtual environment with all Python dependencies | Yes |
| **`runtime`** | Final minimal image with only runtime artefacts | No |

Build tools, compilers, package manager caches, and download artefacts never enter the final image.

### Installed Packages

#### Stage 1 — `rpm-builder` (build-time only)

System packages installed via `microdnf`:

| Package | Purpose |
|---------|---------|
| `yum-utils` | Provides `yumdownloader` to download RPMs without installing |
| `cpio` | Required by `unpackRPM.sh` (`rpm2cpio \| cpio`) |
| `wget` | Downloads the Eclipse MAT archive |
| `unzip` | Extracts the MAT ZIP |

Java RPMs downloaded via `yumdownloader` and extracted with `rpm2cpio`:

| RPM Package | Provides |
|-------------|----------|
| `java-17-openjdk-headless` | JVM runtime (OpenJDK 17, no compiler, ~80 MB) |
| `tzdata-java` | Java timezone database |
| `lksctp-tools` | `libsctp.so` for `java.net` SCTP support |
| `nspr` | Netscape Portable Runtime (`libnspr4.so`, `libplds4.so`, `libplc4.so`) |
| `nss-util` | NSS utility library (`libnssutil3.so`) |
| `nss-softokn` | NSS crypto module (`libsoftokn3.so`) |
| `nss-softokn-freebl` | FIPS-capable freebl crypto (`libfreebl3.so`, `libfreeblpriv3.so`) |

Eclipse MAT 1.16.1 is downloaded from eclipse.org and extracted to `/opt/eclipse-mat`.

#### Stage 2 — `pip-builder` (build-time only)

System packages installed via `microdnf`:

| Package | Purpose |
|---------|---------|
| `python3` | CPython 3.9 interpreter |
| `python3-pip` | Package installer |
| `python3-devel` | Python C headers for compiling extensions |
| `gcc` | C compiler for building native wheels |
| `libxml2-devel` | Development headers for `lxml` |
| `libxslt-devel` | Development headers for `lxml` XSLT support |

Python packages installed via `pip` into `/opt/venv`:

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | >= 0.111.0 | REST API framework |
| `uvicorn[standard]` | >= 0.29.0 | ASGI server (with uvloop, httptools, watchfiles) |
| `pydantic` | >= 2.0.0 | Request/response data validation |
| `pydantic-settings` | >= 2.0.0 | Environment-variable-based configuration |
| `beautifulsoup4` | >= 4.12.0 | HTML parsing for MAT report extraction |
| `lxml` | >= 5.0.0 | Fast HTML parser backend for BeautifulSoup |
| `python-multipart` | >= 0.0.9 | Required by FastAPI for file uploads |

#### Stage 3 — `runtime` (final image)

System packages installed via `microdnf`:

| Package | Purpose |
|---------|---------|
| `python3` | CPython 3.9 interpreter |
| `bash` | Shell for entrypoint script |
| `unzip` | Extracting ZIP report contents at runtime |
| `fontconfig` | Font configuration library required by Java AWT/BIRT chart rendering |
| `dejavu-sans-fonts` | Basic font set for MAT pie charts and report graphics |

Artefacts copied from builder stages:

| Source | Destination | Content |
|--------|-------------|---------|
| `rpm-builder` | `/usr/lib/jvm` | Java 17 JRE (OpenJDK headless) |
| `rpm-builder` | `/etc/java`, `/etc/.java` | JRE security configuration (`java.security`, `nss.cfg`, etc.) |
| `rpm-builder` | `/usr/share/javazi*` | Java timezone data |
| `rpm-builder` | `/usr/lib64/lib*.so` | NSS/NSPR/SCTP shared libraries + FIPS checksum files |
| `rpm-builder` | `/opt/eclipse-mat` | Eclipse MAT 1.16.1 |
| `pip-builder` | `/opt/venv` | Python virtual environment with all dependencies |
| Build context | `/opt/mat-service` | FastAPI application and analyzers |
| Build context | `/usr/local/bin/entrypoint.sh` | Container entrypoint script |

### Image Details

| Property | Value |
|----------|-------|
| Base image | `registry.access.redhat.com/ubi9/ubi-minimal:latest` |
| Final image size | ~594 MB |
| Java | OpenJDK 17 (headless) |
| Python | 3.9 (RHEL 9 system Python) |
| Eclipse MAT | 1.16.1 |
| Runtime user | `mat` (UID 1001, non-root) |
| Exposed port | 8080 |
| Volumes | `/heapdumps`, `/reports` |

### Container Paths

| Path | Description |
|------|-------------|
| `/opt/eclipse-mat/ParseHeapDump.sh` | MAT executable |
| `/opt/eclipse-mat/MemoryAnalyzer.ini` | MAT JVM configuration (`-Xmx32g -Xms4g`) |
| `/opt/mat-service/` | Python backend code |
| `/opt/venv/` | Python virtual environment |
| `/usr/lib/jvm/jre-17-openjdk` | Java 17 JRE home |
| `/usr/local/bin/entrypoint.sh` | Container entrypoint |
| `/heapdumps` | Upload directory for `.hprof` files (volume mount) |
| `/reports` | Output directory for MAT ZIP reports (volume mount) |

### Why Not `ubi9-micro`?

The runtime stage uses `ubi9-minimal` rather than the smaller `ubi9-micro`. This is
a deliberate choice — `ubi9-micro` is designed for single statically-compiled
binaries (Go, Rust, GraalVM native-image) and lacks the runtime infrastructure
this service requires.

**No package manager.** `ubi9-micro` ships without `microdnf`, `dnf`, or any RPM
database. The runtime stage installs five packages (`python3`, `bash`, `unzip`,
`fontconfig`, `dejavu-sans-fonts`) via `microdnf install`. This is impossible on
`ubi9-micro`.

**Missing shared libraries.** CPython, lxml, and Java AWT depend on shared
libraries that exist in `ubi9-minimal` but not in `ubi9-micro`:

| Library | Required By |
|---------|-------------|
| `libpython3.9.so` | CPython interpreter (FastAPI / uvicorn) |
| `libxml2.so.2`, `libxslt.so.1` | lxml (HTML parsing for MAT reports) |
| `libfontconfig.so.1`, `libfreetype.so.6` | Java AWT font rendering (MAT charts) |
| `libffi.so`, `libssl.so`, `libcrypto.so` | CPython stdlib (ctypes, ssl, hashlib) |
| `libharfbuzz.so.0`, `libpng16.so.16`, `libexpat.so.1` | Transitive deps of freetype / fontconfig |

**Fontconfig requires more than libraries.** Java AWT uses fontconfig to discover
fonts for chart rendering. Fontconfig needs configuration files (`/etc/fonts/`),
pre-built cache (`/usr/lib/fontconfig/cache/`), and actual font files
(`/usr/share/fonts/`). Without a coherent fontconfig installation, Java's
`X11FontManager` throws `"Fontconfig head is null"` and MAT report generation
fails — a [known issue on ubi9-micro](https://github.com/quarkusio/quarkus/issues/49226).

**Multi-stage COPY workarounds are fragile.** You can technically COPY individual
`.so` files and font assets from a builder stage (the
[Quarkus AWT Dockerfile](https://github.com/quarkusio/quarkus-quickstarts/blob/main/awt-graphics-rest-quickstart/src/main/docker/Dockerfile.native-micro)
demonstrates this pattern), but:

- Library paths change between UBI minor releases, silently breaking builds
- Transitive dependency discovery is manual — when Red Hat updates fontconfig or
  freetype and adds a new dependency, the build succeeds but the container crashes
- Font cache files are architecture-specific binaries that must match the freetype
  version; stale caches cause null-pointer crashes
- The CPython ABI coupling means the interpreter, stdlib `.so` extensions, and venv
  must all come from the same build — approximately 2000+ files to COPY manually

For this service, the ~5 MB overhead of `ubi9-minimal` over `ubi9-micro` buys a
working package manager and eliminates a fragile manifest of 30+ individual library
paths. The trade-off strongly favours `ubi9-minimal`.

---

## API Reference

| Method | Path | Response | Description |
|--------|------|----------|-------------|
| `GET` | `/health` | JSON | Liveness probe |
| `GET` | `/reports` | JSON | List ZIP reports in `/reports`, grouped by type |
| `POST` | `/analyze/heapdump/report` | `text/plain` | Upload `.hprof` → run MAT → human-readable report |
| `POST` | `/analyze/heapdump` | JSON | Upload `.hprof` → run MAT → structured analysis |
| `POST` | `/analyze/suspects` | JSON | Analyse an existing Leak Suspects ZIP |
| `POST` | `/analyze/overview` | JSON | Analyse an existing System Overview ZIP |
| `POST` | `/analyze/top-components` | JSON | Analyse an existing Top Components ZIP |
| `POST` | `/analyze/all` | JSON | Auto-discover and run all three analyzers |
| `GET` | `/docs` | HTML | Swagger UI |

### `/analyze/heapdump/report` — form fields

| Field | Default | Description |
|-------|---------|-------------|
| `file` *(required)* | -- | `.hprof` heap dump file |
| `sections` | `suspects,overview,top_components` | Comma-separated subset to include |
| `heapdumps_dir` | `/heapdumps` | Container path where the upload is saved |
| `reports_dir` | `/reports` | Container path for MAT ZIP output |

```bash
# Only the Leak Suspects section
curl -s -X POST http://localhost:8080/analyze/heapdump/report \
     -F "file=@./heapdumps/myapp.hprof" \
     -F "sections=suspects"
```

### `/analyze/all` — JSON body

```json
{ "reports_dir": "/reports", "output_dir": null, "include_text": true }
```

### `/analyze/suspects` (and `/overview`, `/top-components`) — JSON body

```json
{ "report_path": "/reports/myapp_Leak_Suspects.zip", "output_dir": null, "include_text": true }
```

---

## Generate Heap Dumps

### Option A -- Bundled Java Demo

The project includes `demo/src/JavaMemoryIssuesDemo.java`, which simulates 7
common Java memory problems and captures `.hprof` heap dumps.

**Requirements:** Java 11+ on your `PATH`.

```bash
chmod +x demo/run-demo.sh
./demo/run-demo.sh all     # All 7 scenarios
./demo/run-demo.sh 3       # Single scenario
./demo/run-demo.sh menu    # Interactive menu
```

Output files appear in `heapdumps/`:

| # | Scenario | Pattern |
|---|----------|---------|
| 1 | Static Collection Leak | `static List` that never shrinks |
| 2 | Cache Without Eviction | `HashMap` used as cache, no size limit |
| 3 | Event Listener Leak | Listeners registered, never removed |
| 4 | ThreadLocal Leak | `ThreadLocal` not cleaned up in thread pools |
| 5 | String Duplication | Thousands of identical `String` objects |
| 6 | ClassLoader / Resource Leak | Large object graphs held by loaders |
| 7 | Large Object Allocation | Continuous array allocation until OOM |

### Option B -- Dump a Running JVM

```bash
# jmap (classic)
jmap -dump:format=b,file=./heapdumps/myapp.hprof <PID>

# jcmd (preferred for JDK 9+)
jcmd <PID> GC.heap_dump ./heapdumps/myapp.hprof

# Auto-dump on OutOfMemoryError
-XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/heapdumps/oom.hprof
```

---

## Batch Analysis

Analyse all heap dumps in a single pass:

### Plain-text reports

```bash
HEAPDUMP_DIR="./heapdumps"
REPORT_DIR="./reports/text"
mkdir -p "$REPORT_DIR"

for hprof in "$HEAPDUMP_DIR"/*.hprof; do
    name="$(basename "$hprof" .hprof)"
    echo "Analysing $name ..."
    curl -s -X POST http://localhost:8080/analyze/heapdump/report \
         -F "file=@$hprof" > "$REPORT_DIR/${name}.txt"
done
```

### JSON reports

```bash
mkdir -p ./reports/json
for hprof in ./heapdumps/*.hprof; do
    name="$(basename "$hprof" .hprof)"
    curl -s -X POST http://localhost:8080/analyze/heapdump \
         -F "file=@$hprof" | python3 -m json.tool > "./reports/json/${name}.json"
done
```

### Parallel execution (GNU parallel)

```bash
mkdir -p ./reports/text
ls ./heapdumps/*.hprof | parallel -j4 \
  'name=$(basename {} .hprof); \
   curl -s -X POST http://localhost:8080/analyze/heapdump/report \
        -F "file=@{}" > ./reports/text/${name}.txt && echo "done: $name"'
```

---

## Configuration

### Environment variables

#### Core settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MAT_TIMEOUT` | `600` | Seconds before MAT subprocess is killed |
| `UVICORN_WORKERS` | `4` | Number of uvicorn worker processes |
| `MAX_UPLOAD_SIZE_BYTES` | `21474836480` | Upload file size limit in bytes (20 GB); returns HTTP 413 if exceeded |
| `LOG_LEVEL` | `INFO` | Root log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_JSON` | `false` | Set to `true` for JSON-structured logging (one object per line, for ELK/CloudWatch) |

#### Analyzer thresholds

All analyzer thresholds are configurable via environment variables. Defaults match
the original hardcoded values. Prefix determines the analyzer:

| Prefix | Analyzer | Example variable |
|--------|----------|------------------|
| `SUSPECTS_` | Leak Suspects | `SUSPECTS_PRIMARY_LEAK_HIGH_MB=500` |
| `OVERVIEW_` | System Overview | `OVERVIEW_LARGE_HEAP_HIGH_MB=2048` |
| `TOP_COMPONENTS_` | Top Components | `TOP_COMPONENTS_DOMINANT_CONSUMER_MB=500` |

See `backend/config.py` for the full list of threshold settings and their defaults.

```bash
docker run -d \
  -e MAT_TIMEOUT=3600 \
  -e UVICORN_WORKERS=8 \
  --platform linux/amd64 \
  -p 8080:8080 \
  -v $(pwd)/heapdumps:/heapdumps \
  -v $(pwd)/reports:/reports \
  eclipse-mat
```

### MAT JVM memory (large heap dumps)

MAT is pre-configured with `-Xmx32g -Xms4g`. For dumps larger than ~16 GB,
mount a custom `MemoryAnalyzer.ini`:

```bash
cat > MemoryAnalyzer.ini <<'EOF'
-vmargs
-Xmx64g
-Xms8g
-XX:+UseG1GC
-XX:+UseStringDeduplication
-XX:+ParallelRefProcEnabled
-Djava.io.tmpdir=/mat-work
EOF

docker run -d --platform linux/amd64 \
  -v $(pwd)/MemoryAnalyzer.ini:/opt/eclipse-mat/MemoryAnalyzer.ini \
  -p 8080:8080 \
  eclipse-mat
```

### Production Deployment (20 Concurrent Users)

The service runs multiple uvicorn worker processes, each offloading CPU-bound
MAT and analyzer work to a thread pool. Sizing depends primarily on **heap dump
size** — MAT requires approximately 2x the dump size in RAM per analysis.

#### Hardware Recommendations

| Heap Dump Size | CPU | RAM | Disk | `UVICORN_WORKERS` | `MAT_TIMEOUT` |
|----------------|-----|-----|------|-------------------|---------------|
| Small (< 1 GB) | 8 cores | 32 GB | 100 GB SSD | `8` | `300` |
| Medium (1–5 GB) | 16 cores | 128 GB | 500 GB SSD | `8` | `600` |
| Large (5–16 GB) | 32 cores | 256 GB | 1 TB NVMe | `8` | `1800` |

**Why these numbers:** With 20 concurrent users and 8 workers, up to 8 MAT
analyses can run in parallel. Each analysis spawns a MAT subprocess that needs
~2x the dump size in RAM. The remaining 12 requests queue and are served as
workers become available. Python analyzers (parsing ZIPs) are lightweight and
do not bottleneck.

#### Recommended production `docker run`

```bash
docker run -d \
  --name mat-service \
  --platform linux/amd64 \
  --cpus 16 \
  --memory 128g \
  -p 8080:8080 \
  -e UVICORN_WORKERS=8 \
  -e MAT_TIMEOUT=1200 \
  -v /data/heapdumps:/heapdumps \
  -v /data/reports:/reports \
  eclipse-mat
```

#### Sizing Guidelines

- **CPU:** Allocate 2 cores per uvicorn worker. With `UVICORN_WORKERS=8`, use
  at least 16 cores so MAT subprocesses and Python analyzers do not starve each
  other.
- **RAM:** The dominant consumer is MAT. Each concurrent MAT analysis needs
  ~2x the dump size. For 8 parallel analyses of 5 GB dumps:
  `8 × 10 GB = 80 GB` plus OS + Python overhead → **128 GB** recommended.
  Adjust `MemoryAnalyzer.ini` `-Xmx` to match.
- **Disk:** Each upload is saved to `/heapdumps` and MAT generates temporary
  index files (~1.5x dump size) plus ZIP reports. Use fast SSD/NVMe to avoid
  I/O bottlenecks. Provision at least `3 × max_dump_size × UVICORN_WORKERS`.
- **Workers:** Do not set `UVICORN_WORKERS` higher than the number of available
  CPU cores. 8 workers handles 20 concurrent users well — requests beyond the
  worker count queue briefly. For ZIP-only analysis endpoints (no MAT), workers
  are freed quickly.
- **Timeout:** Set `MAT_TIMEOUT` to at least `3 × expected_analysis_seconds`.
  A 5 GB dump typically takes 3–8 minutes; set `MAT_TIMEOUT=1200` (20 min) to
  cover worst-case I/O contention under load.

#### Scaling Beyond 20 Users

For higher concurrency, run multiple containers behind a load balancer (nginx,
HAProxy, or Kubernetes Service). Each container instance handles its own set of
workers. Shared storage (NFS, EFS) for `/heapdumps` and `/reports` is required
in multi-instance deployments. Upload filenames are UUID-prefixed to prevent
collisions across instances.

---

## Deploy on OpenShift (Helm)

A Helm chart is provided at `helm/eclipse-mat-service/` for deploying on OpenShift.

```bash
# Install
helm install mat-service helm/eclipse-mat-service/ \
  --set image.repository=your-registry.io/eclipse-mat \
  --set image.tag=latest

# Custom values file
helm install mat-service helm/eclipse-mat-service/ -f my-values.yaml

# Upgrade
helm upgrade mat-service helm/eclipse-mat-service/

# Uninstall
helm uninstall mat-service
```

The chart creates: Deployment, Service (ClusterIP), OpenShift Route (TLS edge),
two PVCs (`/heapdumps` 50Gi, `/reports` 10Gi), ConfigMap (all env vars), and
ServiceAccount. All values are configurable — see `helm/eclipse-mat-service/values.yaml`.

Key overrides:

```bash
# Large production instance
helm install mat-service helm/eclipse-mat-service/ \
  --set resources.requests.memory=8Gi \
  --set resources.limits.memory=40Gi \
  --set persistence.heapdumps.size=200Gi \
  --set config.matTimeout=3600

# Disable Route (internal-only)
helm install mat-service helm/eclipse-mat-service/ --set route.enabled=false

# Disable persistent storage (ephemeral)
helm install mat-service helm/eclipse-mat-service/ \
  --set persistence.heapdumps.enabled=false \
  --set persistence.reports.enabled=false
```

---

## Running Tests

```bash
cd backend
pip install -r requirements-test.txt
python -m pytest tests/ -v
```

Tests use in-memory synthetic ZIP fixtures — no heap dumps or Eclipse MAT
installation required.

---

## Project Structure

```
eclipse-mat-service/
├── README.md
│
├── backend/                            # Python REST service
│   ├── app.py                          # App factory (~57 lines) — creates FastAPI instance
│   ├── config.py                       # Pydantic BaseSettings: all config + analyzer thresholds
│   ├── logging_config.py               # Structured JSON logging (LOG_JSON=true)
│   ├── models.py                       # Pydantic request/response models
│   ├── exceptions.py                   # Centralized exception handlers
│   ├── main.py                         # Local dev entry point (uvicorn.run)
│   ├── requirements.txt                # Python dependencies (production)
│   ├── requirements-test.txt           # Test dependencies (pytest, httpx)
│   ├── routes/
│   │   ├── operations.py               # /health (with disk info), /reports
│   │   └── analysis.py                 # All /analyze/* routes
│   ├── services/
│   │   ├── mat_runner.py               # MAT subprocess execution
│   │   └── analysis_service.py         # Analyzer orchestration + heapdump pipeline
│   ├── analyzers/
│   │   ├── __init__.py                 # Exports three analyzer classes
│   │   ├── base.py                     # MATBaseAnalyzer: ZIP extraction, HTML parsing
│   │   ├── suspects.py                 # MATLeakSuspectsAnalyzer
│   │   ├── overview.py                 # MATSystemOverviewAnalyzer
│   │   ├── top_components.py           # MATTopComponentsAnalyzer
│   │   └── java_recommendations.json   # 16 diagnostic patterns (externalized)
│   └── tests/
│       ├── conftest.py                 # Shared fixtures (synthetic ZIPs, TestClient)
│       ├── test_app.py                 # Route and helper tests
│       ├── test_suspects_analyzer.py   # Leak Suspects analyzer tests
│       ├── test_overview_analyzer.py   # System Overview analyzer tests
│       └── test_top_components_analyzer.py  # Top Components analyzer tests
│
├── docker/                             # Docker build
│   ├── Dockerfile                      # 3-stage multi-stage build (UBI9-minimal)
│   └── scripts/
│       ├── entrypoint.sh               # REST service entrypoint
│       └── unpackRPM.sh               # Extracts RPMs via rpm2cpio (build-time)
│
├── demo/                               # Java demo application
│   ├── src/
│   │   └── JavaMemoryIssuesDemo.java  # 7 memory-issue scenarios
│   └── run-demo.sh                     # Compile & run helper
│
├── helm/                               # Helm chart for OpenShift deployment
│   └── eclipse-mat-service/
│       ├── Chart.yaml                  # Chart metadata (v0.1.0, appVersion 3.1.0)
│       ├── values.yaml                 # All configurable defaults
│       └── templates/                  # K8s/OpenShift resource templates
│           ├── _helpers.tpl            # Template helper functions
│           ├── deployment.yaml         # Deployment with probes, PVCs, ConfigMap
│           ├── service.yaml            # ClusterIP Service (port 8080)
│           ├── route.yaml              # OpenShift Route (TLS edge)
│           ├── configmap.yaml          # All env vars from config.py
│           ├── pvc-heapdumps.yaml      # PVC for /heapdumps (50Gi)
│           ├── pvc-reports.yaml        # PVC for /reports (10Gi)
│           └── serviceaccount.yaml     # ServiceAccount with pull secrets
│
├── heapdumps/                          # Volume mount: .hprof files
└── reports/                            # Volume mount: MAT ZIP reports
```

---

## Troubleshooting

**`mat_available: false` in `/health`**

MAT was not found at `/opt/eclipse-mat/ParseHeapDump.sh`. The Docker build
probably failed to download MAT (network issue). Rebuild the image. The
`/analyze/suspects`, `/analyze/overview`, and `/analyze/top-components` endpoints
still work with pre-generated ZIPs and do not require MAT.

**MAT times out on large heap dumps**

Set `MAT_TIMEOUT` to a higher value (e.g. `3600` for 1 hour). Ensure the
container has enough RAM -- MAT requires approximately 2x the heap dump size.

**`Only .hprof heap dump files are accepted`**

The upload endpoint validates the file extension. Rename the file to end with
`.hprof` if it was saved with a different extension.

**Permission denied errors in container**

The container runs as non-root user `mat` (UID 1001). If volume-mounted
directories have restrictive host permissions, the container cannot write to
them. Fix with:

```bash
chmod 777 ./heapdumps ./reports
```

Or match the UID:

```bash
docker run --user 1001:1001 ...
```

**Apple Silicon: image fails or hangs**

MAT is x86-only. Always add `--platform linux/amd64` to `docker build` and
`docker run`. Omitting this silently fails or produces a non-functional image.

**`python-multipart` not installed (running outside Docker)**

```bash
pip install -r backend/requirements.txt
```
