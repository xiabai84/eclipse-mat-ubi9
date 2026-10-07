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
  --memory=16g \
  -p 8080:8080 \
  -e API_TOKEN=change-me \
  -v $(pwd)/heapdumps:/heapdumps \
  eclipse-mat
```

`API_TOKEN` is optional: without it the analysis endpoints are open to anyone who can reach the service (a warning is
logged). MAT's heap is derived from the memory limit (75 %), see [Configuration](#configuration).

### 3. Verify

```bash
curl http://localhost:8080/health
```

```json
{
  "status": "ok",
  "service": "mat-analysis",
  "version": "4.0.0",
  "mat_available": true,
  "disk": {
    "heapdumps": { "free_gb": 42.5, "total_gb": 100.0 }
  }
}
```

Browse the interactive API docs at **http://localhost:8080/docs**.

### 4. Analyse a heap dump

```bash
# Human-readable report (.hprof or .hprof.gz)
curl -s -X POST http://localhost:8080/analyze/heapdump/report \
     -H "Authorization: Bearer $API_TOKEN" \
     -F "file=@./heapdumps/myapp.hprof.gz"

# Structured JSON response
curl -s -X POST http://localhost:8080/analyze/heapdump \
     -H "Authorization: Bearer $API_TOKEN" \
     -F "file=@./heapdumps/myapp.hprof.gz" | python3 -m json.tool
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
│  │  Top_Components.zip  ───►│  POST /analyze/heapdump/report│    │
│  └─────────────────────┘    │    • same, as plain text     │     │
│                             └──────────────────────────────┘     │
│  ┌──────────────────────┐                                        │
│  │  Python Analyzers    │   Volume mount                         │
│  │  suspects.py         │   /heapdumps/job-<id>/  one directory  │
│  │  overview.py         │     per request: dump, MAT index and   │
│  │  top_components.py   │     report ZIPs - removed afterwards   │
│  └──────────────────────┘                                        │
└──────────────────────────────────────────────────────────────────┘
```

**Data flow:**
```
Upload .hprof(.gz) → /heapdumps/job-<id>/ (decompress) → wait for a MAT slot → run MAT → report ZIPs next
  to the dump → parse with BeautifulSoup → JSON or text → remove /heapdumps/job-<id>/ (also on failure)
```

| Workflow | Endpoint | Response |
|----------|----------|----------|
| Upload & analyse (human-readable) | `POST /analyze/heapdump/report` | `text/plain` |
| Upload & analyse (machine-readable) | `POST /analyze/heapdump` | JSON |

Concurrent requests never share files. At most `MAT_MAX_CONCURRENT` MAT runs happen at the same time across all
worker processes; the others wait up to `MAT_QUEUE_TIMEOUT_SECONDS`, then get `503`.

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
| `fastapi` | 0.128.8 | REST API framework |
| `uvicorn[standard]` | 0.39.0 | ASGI server (with uvloop, httptools, watchfiles) |
| `pydantic` | 2.13.5 | Request/response data validation |
| `pydantic-settings` | 2.11.0 | Environment-variable-based configuration |
| `beautifulsoup4` | 4.15.0 | HTML parsing for MAT report extraction |
| `lxml` | 6.1.3 | Fast HTML parser backend for BeautifulSoup |
| `python-multipart` | 0.0.20 | Required by FastAPI for file uploads |

Versions are pinned exactly (tested with Python 3.9 as in UBI 9); the MAT download is checked against Eclipse's
SHA-512 (`MAT_SHA512` build arg).

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
| Base image | `registry.access.redhat.com/ubi9/ubi-minimal:9.8` (build arg `UBI_TAG`) |
| Final image size | ~594 MB |
| Java | OpenJDK 17 (headless) |
| Python | 3.9 (RHEL 9 system Python) |
| Eclipse MAT | 1.16.1 |
| Runtime user | `mat` (UID 1001, non-root) |
| Exposed port | 8080 |
| Volumes | `/heapdumps` |

### Container Paths

| Path | Description |
|------|-------------|
| `/opt/eclipse-mat/ParseHeapDump.sh` | MAT executable |
| `/opt/eclipse-mat/MemoryAnalyzer.ini` | MAT's own JVM configuration (`-Xmx1024m`) – **not used for the heap**: the service passes `-vmargs` with `MAT_XMX`, which replaces it |
| `/opt/mat-service/` | Python backend code |
| `/opt/venv/` | Python virtual environment |
| `/usr/lib/jvm/jre-17-openjdk` | Java 17 JRE home |
| `/usr/local/bin/entrypoint.sh` | Container entrypoint |
| `/heapdumps` | One work directory per request (dump, MAT index files, report ZIPs), removed afterwards (volume mount) |

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
| `GET` | `/health` | JSON | Liveness probe (never requires the token) |
| `POST` | `/analyze/heapdump/report` | `text/plain` | Upload `.hprof` / `.hprof.gz` → run MAT → human-readable report |
| `POST` | `/analyze/heapdump` | JSON | Upload `.hprof` / `.hprof.gz` → run MAT → structured analysis |
| `GET` | `/docs` | HTML | Swagger UI |

With `API_TOKEN` set, both `POST` endpoints require `Authorization: Bearer <API_TOKEN>` (`401` otherwise).

### Form fields

| Field | Default | Description |
|-------|---------|-------------|
| `file` *(required)* | -- | `.hprof` or `.hprof.gz` heap dump |
| `sections` *(report only)* | `suspects,overview,top_components` | Comma-separated subset to include |

```bash
# Only the Leak Suspects section
curl -s -X POST http://localhost:8080/analyze/heapdump/report \
     -H "Authorization: Bearer $API_TOKEN" \
     -F "file=@./heapdumps/myapp.hprof.gz" \
     -F "sections=suspects"
```

### Errors

| Status | Meaning |
|--------|---------|
| `400` | not a `.hprof` / `.hprof.gz`, or the `.gz` cannot be decompressed |
| `401` | `API_TOKEN` set and the bearer token missing or wrong |
| `413` | larger than `MAX_UPLOAD_SIZE_BYTES` (checked from `Content-Length` before the upload is read) or, decompressed, than `MAX_DUMP_SIZE_BYTES` |
| `502` | MAT failed or produced no report (e.g. out of memory: raise `MAT_XMX` / the memory limit) – never a stale report |
| `503` | no MAT slot free within `MAT_QUEUE_TIMEOUT_SECONDS` (`Retry-After` header) |
| `504` | MAT exceeded `MAT_TIMEOUT`; the MAT process is killed |

### What the analysis reports

- **Leak suspects** with the *accumulation point* and the field path that keeps it alive, e.g.
  `JavaMemoryIssuesDemo.STATIC_SESSIONS (static) → java.util.ArrayList.elementData → java.lang.Object[]`, and the
  thread when MAT names one (`worker-tl (keeps local variables of 60.0 MB)`). Suspects below `SUSPECTS_MIN_PROBLEM_MB`
  or `SUSPECTS_MIN_PROBLEM_PCT` are listed but are no problems – MAT names suspects on every heap.
- **System overview**: heap, objects, classes, GC roots (object and GC-root counts are warnings, not problems),
  class histogram, threads that retain much memory.
- **Top components**: memory per class loader, the biggest objects, and MAT's own verdicts of its waste checks.
  Empty collections, duplicate content and the like count as waste; collections with low fill ratios are reported
  as warnings with the memory they *retain* (that is not waste and often overlaps with empty collections).

The results contain class and field names, thread names and object addresses, but not the contents of strings
(MAT's report pages do show them).

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
| `API_TOKEN` | *(empty)* | When set, the analysis endpoints require `Authorization: Bearer <API_TOKEN>`. Empty: open, with a warning in the log |
| `MAT_XMX` | *(empty)* | MAT's JVM heap, e.g. `12g`. Empty: 75 % of the container memory limit divided by `MAT_MAX_CONCURRENT` (fallback `4g` without a limit) |
| `MAT_MAX_CONCURRENT` | `1` | MAT runs at the same time, across all worker processes |
| `MAT_QUEUE_TIMEOUT_SECONDS` | `3600` | How long a request waits for a free MAT slot before `503` |
| `MAT_TIMEOUT` | `3600` | Seconds one MAT run may take; then MAT is killed (`504`) |
| `MAX_UPLOAD_SIZE_BYTES` | `21474836480` | Upload limit (20 GB; the compressed size for `.hprof.gz`) |
| `MAX_DUMP_SIZE_BYTES` | `68719476736` | Limit after decompressing a `.hprof.gz` (64 GB) |
| `HEAPDUMPS_DIR` | `/heapdumps` | Work directories (one per request) |
| `UVICORN_WORKERS` | `4` | uvicorn worker processes (they share the MAT slots) |
| `LOG_LEVEL` | `INFO` | Root log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_JSON` | `false` | `true` for JSON-structured logging (one object per line) |

#### Analyzer thresholds

All analyzer thresholds are configurable via environment variables; the prefix selects the analyzer:

| Prefix | Analyzer | Example variable |
|--------|----------|------------------|
| `SUSPECTS_` | Leak Suspects | `SUSPECTS_MIN_PROBLEM_MB=10`, `SUSPECTS_MIN_PROBLEM_PCT=10` |
| `OVERVIEW_` | System Overview | `OVERVIEW_LARGE_HEAP_HIGH_MB=2048` |
| `TOP_COMPONENTS_` | Top Components | `TOP_COMPONENTS_DOMINANT_CONSUMER_MB=500` |

See `backend/config.py` for the full list of threshold settings and their defaults.

### MAT JVM memory

MAT needs roughly 1.5–2× the dump size as heap. The service starts MAT with
`-vmargs --add-exports=… -Xmx<MAT_XMX> -Duser.language=en -Duser.country=US`; on the command line, `-vmargs` replaces
the options in `MemoryAnalyzer.ini` (whose `-Xmx1024m` would otherwise apply), so do not mount a custom ini – set
the container memory limit, or `MAT_XMX`. The English locale keeps MAT's number format stable (the analyzers read
both formats). A run that fails with `OutOfMemoryError` returns `502` with a hint to raise the memory.

```bash
docker run -d --platform linux/amd64 \
  --memory 32g \
  -e MAT_MAX_CONCURRENT=1 \
  -p 8080:8080 \
  -v $(pwd)/heapdumps:/heapdumps \
  eclipse-mat                         # MAT heap: 75 % of 32 GB = 24 GB
```

### Production sizing

Requests wait synchronously until their analysis is done. Sizing follows the **dump size** and
`MAT_MAX_CONCURRENT`:

| Heap dump size | RAM limit per parallel MAT run | Disk in `/heapdumps` per run | `MAT_TIMEOUT` |
|----------------|--------------------------------|------------------------------|---------------|
| < 1 GB | 4 GB | 3 GB | `600` |
| 1–5 GB | 16 GB | 15 GB | `1800` |
| 5–16 GB | 48 GB | 50 GB | `3600` |

- **Memory:** container limit ≈ `MAT_MAX_CONCURRENT × (dump size × 2) / 0.75`. Keep `MAT_MAX_CONCURRENT=1` unless
  the limit allows more; requests beyond it queue.
- **Disk:** each run needs the dump, MAT's index files (~1–1.5× the dump) and the reports – and a `.hprof.gz`
  upload is decompressed first. Uploads are also buffered in `/tmp` before they reach `/heapdumps`.
- **Timeouts in front of the service** (ingress, router, load balancer) must exceed upload time +
  `MAT_QUEUE_TIMEOUT_SECONDS` + `MAT_TIMEOUT`, or clients see a gateway timeout while MAT keeps working.

#### Scaling out

Several instances behind a load balancer each have their own MAT slots; `/heapdumps` does not need to be shared
(every request lives in its own directory on the instance that received it).

---

## Deploy on OpenShift (Helm)

A Helm chart is provided at `helm/` for deploying on OpenShift. The image tag defaults to the chart's `appVersion`.

```bash
# Install, with a token from an existing Secret (key "api-token")
oc create secret generic mat-api-token --from-literal=api-token="$(openssl rand -hex 24)"
helm install mat-service helm/ \
  --set image.repository=your-registry.io/eclipse-mat \
  --set auth.existingSecret=mat-api-token

# Custom values file
helm install mat-service helm/ -f my-values.yaml

# Upgrade
helm upgrade mat-service helm/

# Uninstall
helm uninstall mat-service
```

The chart creates: Deployment, Service (ClusterIP), OpenShift Route (TLS edge, 2 h timeouts – the request waits for
the analysis), a PVC for `/heapdumps` (50Gi), ConfigMap (all env vars), ServiceAccount, and – with `auth.apiToken` –
a Secret. `/tmp` is an emptyDir with `persistence.tmpSizeLimit` (uploads are buffered there). All values are
configurable – see `helm/values.yaml`.

Key overrides:

```bash
# Large production instance
helm install mat-service helm/ \
  --set resources.requests.memory=8Gi \
  --set resources.limits.memory=40Gi \
  --set persistence.heapdumps.size=200Gi \
  --set config.matTimeout=3600                 # MAT heap: 75 % of 40Gi = 30 GB

# Disable Route (internal-only)
helm install mat-service helm/ --set route.enabled=false

# Disable persistent storage (ephemeral)
helm install mat-service helm/ --set persistence.heapdumps.enabled=false
```

---

## Running Tests

```bash
cd backend
pip install -r requirements-test.txt
python -m pytest tests/ -v
```

The analyzer tests run against **real Eclipse MAT 1.16.1 reports** in `tests/fixtures/mat/` (HTML text, zipped at
test time), made by `tests/fixtures/make_fixtures.py` from three heap dumps with known content (demo scenario 1, a
healthy 2 MB heap, and `tests/integration/GroundTruth.java`); local paths, user and host names are scrubbed. The
service tests use a stand-in for MAT. Neither needs MAT installed.

With a real MAT (Linux), the integration test builds the ground-truth dump and runs it through the service:

```bash
MAT_SCRIPT=/opt/eclipse-mat/ParseHeapDump.sh python -m pytest tests/integration -v
```

CI (`.github/workflows/ci.yml`) runs the unit tests (Python 3.9 and 3.12), the integration test with the
checksum-verified MAT, and builds the image and smoke-tests it with a memory limit and a token.

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
│   ├── auth.py                         # Optional bearer token (API_TOKEN)
│   ├── exceptions.py                   # Centralized exception handlers
│   ├── main.py                         # Local dev entry point (uvicorn.run)
│   ├── requirements.txt                # Python dependencies (production)
│   ├── requirements-test.txt           # Test dependencies (pytest, httpx)
│   ├── routes/
│   │   ├── operations.py               # /health (with disk info)
│   │   └── analysis.py                 # All /analyze/* routes
│   ├── services/
│   │   ├── mat_runner.py               # MAT run: slots, -vmargs, process-group timeout
│   │   └── analysis_service.py         # Work dir per request → MAT → analyzers → cleanup
│   ├── analyzers/
│   │   ├── __init__.py                 # Exports three analyzer classes
│   │   ├── base.py                     # MATBaseAnalyzer: ZIP extraction, HTML parsing
│   │   ├── suspects.py                 # MATLeakSuspectsAnalyzer
│   │   ├── overview.py                 # MATSystemOverviewAnalyzer
│   │   ├── top_components.py           # MATTopComponentsAnalyzer
│   │   └── java_recommendations.json   # 16 diagnostic patterns (externalized)
│   └── tests/
│       ├── conftest.py                 # Real MAT fixtures (zipped at test time), fake MAT, TestClient
│       ├── fixtures/                   # make_fixtures.py + mat/<fixture>_<report>/ (real MAT HTML, scrubbed)
│       ├── integration/                # GroundTruth.java + test with a real MAT (MAT_SCRIPT)
│       ├── test_app.py                 # Service: auth, uploads, MAT failures, timeouts, concurrency, cleanup
│       ├── test_parsing.py             # Sizes and names as MAT prints them (both locales)
│       ├── test_text_reports.py        # Text reports render for every fixture
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
│           ├── secret.yaml             # API token (only with auth.apiToken)
│           └── serviceaccount.yaml     # ServiceAccount with pull secrets
│
├── heapdumps/                          # Volume mount: .hprof files
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
chmod 777 ./heapdumps
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

---

## OpenShift Troubleshooting

**`pod is forbidden: unable to validate against any security context constraint` / `runAsUser: Invalid value: 1001`**

OpenShift enforces namespace-specific UID ranges via Security Context Constraints
(SCCs). Do **not** set `runAsUser` or `fsGroup` to fixed UIDs in the Helm chart.
The default `values.yaml` uses only `runAsNonRoot: true` and lets OpenShift assign
a UID from the namespace's allocated range.

If you see this error, check your values override:

```bash
# Wrong — fixed UID conflicts with OpenShift SCC
podSecurityContext:
  runAsUser: 1001
  fsGroup: 1001

# Correct — let OpenShift assign the UID
podSecurityContext:
  runAsNonRoot: true
```

**`no usable temporary directory found in [/tmp, /var/tmp, /usr/tmp, /home/mat]`**

The OpenShift-assigned UID cannot write to directories owned by UID 1001 in the
Docker image. The Helm chart mounts `emptyDir` volumes at `/tmp`, `/home/mat`, and
`/opt/eclipse-mat/workspace` to provide writable directories regardless of the
assigned UID.

If you still see this error, verify the `emptyDir` volumes are present:

```bash
oc get deployment <release-name> -o jsonpath='{.spec.template.spec.volumes[*].name}'
# Expected: tmp home-mat mat-workspace heapdumps reports
```

**`there was an error parsing the body` (HTTP 400) on file upload**

The OpenShift HAProxy router has a default request body size limit. The Helm chart
sets `haproxy.router.openshift.io/proxy-body-size: 20g` on the Route. Verify the
annotation is present:

```bash
oc get route <release-name> -o jsonpath='{.metadata.annotations}'
```

If uploading very large dumps (> 1 GB), also ensure the timeout annotations are
sufficient:

```yaml
route:
  annotations:
    haproxy.router.openshift.io/timeout: "1800s"
    haproxy.router.openshift.io/proxy-body-size: "20g"
    haproxy.router.openshift.io/proxy-read-timeout: "1800s"
    haproxy.router.openshift.io/proxy-send-timeout: "1800s"
```

**Upload returns empty response (HTTP status 0) or curl exit code 7**

Two common causes:

1. **Using `http://` instead of `https://`.** The Route redirects HTTP to HTTPS
   (302). With large file uploads, the redirect silently drops the POST body.
   Always use `https://`:

   ```bash
   curl -k -X POST https://<route>/analyze/heapdump/report \
     -F "file=@./dump.hprof"
   ```

2. **HAProxy timeout during upload.** Large heap dumps can take minutes to upload.
   Increase the Route timeout annotations (see above).

**`No matching ZIP found` — MAT runs but produces no reports**

MAT needs write access to its workspace directory inside `/opt/eclipse-mat/`. The
Helm chart mounts an `emptyDir` at `/opt/eclipse-mat/workspace` for this purpose.

If reports are still missing, check the MAT stderr output:

```bash
oc logs deployment/<release-name> | grep -i "error\|permission\|denied\|workspace"
```

Use the JSON endpoint to see the full MAT result including return code and stderr:

```bash
curl -k -X POST https://<route>/analyze/heapdump \
  -F "file=@./dump.hprof" | python3 -m json.tool
```

Look at the `mat.returncode`, `mat.stderr_tail`, and `mat.reports_generated` fields.

**Pod stuck in `CrashLoopBackOff`**

Check logs for the root cause:

```bash
oc logs deployment/<release-name> --previous
```

Common causes: insufficient memory (MAT needs ~2x dump size in RAM), missing
writable directories, or the readiness probe failing because uvicorn cannot start.
