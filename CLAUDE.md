## Workflow Orchestration

### 1. Plan Node Default
- Use superpowers plugin as default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately - don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One tack per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes - don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests - then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management
1. **Plan First**: Write plan to `tasks/todo.md` with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `tasks/todo.md`
6. **Capture Lessons**: Update `tasks/lessons.md` after corrections

## Core Principles
- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid sprawling changes.

---

## Project Overview

Eclipse MAT Service — a Docker-based Java heap analysis suite pairing **Eclipse Memory Analyzer Tool (MAT)** with a **Python FastAPI REST service** to automate `.hprof` heap dump analysis and deliver structured diagnostics.

**Tech Stack:** Python 3.9+ (FastAPI, BeautifulSoup4), Java 17 JRE (Eclipse MAT 1.16.1), Docker (multi-stage build)

## Commands

### Build Docker Image
```bash
# Apple Silicon / ARM (required on M1/M2/M3 Macs)
docker build --platform linux/amd64 -f docker/Dockerfile -t eclipse-mat .

# Linux / x86
docker build -f docker/Dockerfile -t eclipse-mat .
```

### Run the Service
```bash
docker run -d --name mat-service --platform linux/amd64 \
  --memory=16g -e API_TOKEN=change-me \
  -p 8080:8080 \
  -v $(pwd)/heapdumps:/heapdumps \
  eclipse-mat
```

### Verify Service
```bash
curl http://localhost:8080/health          # Liveness probe
# Swagger UI: http://localhost:8080/docs
```

### Generate Demo Heap Dumps (local, requires Java 11+)
```bash
chmod +x demo/run-demo.sh
./demo/run-demo.sh all      # All 7 memory leak scenarios
./demo/run-demo.sh 3        # Single scenario
```

### Deploy on OpenShift (Helm)
```bash
helm install mat-service helm/ \
  --set image.repository=your-registry.io/eclipse-mat \
  --set auth.existingSecret=mat-api-token
helm lint helm/
helm template test helm/
```

### Analyze Heap Dumps
```bash
# JSON response (.hprof or .hprof.gz; the header only when API_TOKEN is set)
curl -s -X POST http://localhost:8080/analyze/heapdump -H "Authorization: Bearer $API_TOKEN" -F "file=@./myapp.hprof.gz"

# Human-readable text report
curl -s -X POST http://localhost:8080/analyze/heapdump/report -H "Authorization: Bearer $API_TOKEN" -F "file=@./myapp.hprof.gz"
```

### Tests
```bash
cd backend && python -m pytest -q --ignore=tests/integration      # real MAT report fixtures, fake MAT for the service
MAT_SCRIPT=/opt/eclipse-mat/ParseHeapDump.sh python -m pytest tests/integration   # real MAT (Linux)
python tests/fixtures/make_fixtures.py <dir> <name> <fixture>     # new fixtures from MAT report ZIPs (scrubbed)
```

## Architecture

```
backend/
├── app.py                          # App factory: FastAPI instance, upload-size check, auth warning
├── config.py                       # Pydantic BaseSettings: all config + analyzer thresholds
├── logging_config.py               # Structured JSON logging (LOG_JSON=true for ELK/CloudWatch)
├── auth.py                         # Optional bearer token (API_TOKEN)
├── exceptions.py                   # Centralized exception handlers
├── main.py                         # Local dev entry point (uvicorn.run)
├── requirements.txt                # Python dependencies
├── routes/
│   ├── operations.py               # /health
│   └── analysis.py                 # POST /analyze/heapdump, /analyze/heapdump/report
├── services/
│   ├── mat_runner.py               # MAT run: slots (file locks), -vmargs, process-group timeout
│   └── analysis_service.py         # Pipeline: work dir per request → (gunzip) → MAT → analyzers → cleanup
└── analyzers/
    ├── __init__.py                 # Exports three analyzer classes
    ├── base.py                     # MATBaseAnalyzer (abstract): ZIP extraction, HTML parsing
    ├── suspects.py                 # MATLeakSuspectsAnalyzer: leak suspect objects, heap %, retained sizes
    ├── overview.py                 # MATSystemOverviewAnalyzer: heap summary, top entries by type
    ├── top_components.py           # MATTopComponentsAnalyzer: largest retained-heap components
    └── java_recommendations.json   # 16 diagnostic patterns (externalized from base.py)
└── tests/
    ├── conftest.py                 # real MAT fixtures (zipped at test time), FAKE_MAT stand-in, service fixture
    ├── fixtures/                   # make_fixtures.py + mat/<fixture>_<report>/ (real MAT HTML, scrubbed)
    ├── integration/                # GroundTruth.java + test_real_mat.py (needs MAT_SCRIPT)
    └── test_*.py                   # parsing, analyzers, text reports, service

.github/workflows/ci.yml            # unit (Py 3.9/3.12), integration (real MAT, SHA-512), container smoke test

docker/
├── Dockerfile                # 3-stage build: rpm-builder → pip-builder → runtime
└── scripts/
    ├── entrypoint.sh         # REST service entrypoint
    └── unpackRPM.sh          # RPM extraction for multi-stage (build-time)

helm/                                 # Helm chart for OpenShift deployment
├── Chart.yaml                        # Chart metadata
├── values.yaml                       # All configurable defaults
└── templates/                        # K8s/OpenShift resource templates (7 resources)

demo/
├── src/JavaMemoryIssuesDemo.java  # 7 memory leak scenarios for testing
└── run-demo.sh                    # Compile & run automation
```

### Data Flow
```
Upload .hprof(.gz) → /heapdumps/job-<id>/ (gunzip) → wait for a MAT slot → MAT writes index + ZIPs next to the
  dump → analyzers parse the ZIPs → JSON or text → remove /heapdumps/job-<id>/ (always, also on failure)
```

### Import Dependency Graph (no circular imports)
```
config.py → (no local imports)
auth.py → config
logging_config.py → config
exceptions.py → (no local imports)
services/ → config, analyzers
routes/ → auth, services
app.py → config, exceptions, logging_config, routes
```

### Analyzer Inheritance Pattern
- `MATBaseAnalyzer` (abstract): ZIP extraction, HTML parsing, formatting utilities
- Java recommendations loaded from `analyzers/java_recommendations.json`
- Subclasses implement `parse_report()` and `generate_report()`
- `build_summary()` matches detected problems against 16 diagnostic patterns
- Analyzer thresholds are configurable via env vars (see Configuration below)

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness probe (no token needed) |
| POST | `/analyze/heapdump` | Upload .hprof / .hprof.gz → JSON analysis |
| POST | `/analyze/heapdump/report` | Upload .hprof / .hprof.gz → human-readable text |

No endpoint takes server paths (removed in 4.0.0: `/analyze/suspects|overview|top-components|all`, `/reports`,
the `heapdumps_dir` / `reports_dir` form fields).

## Environment & Configuration

### Core Settings (config.py `Settings`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `API_TOKEN` | *(empty)* | Bearer token for the analysis endpoints; empty = open (warning logged) |
| `MAT_XMX` | *(empty)* | MAT heap; empty = 75 % of the cgroup memory limit / `MAT_MAX_CONCURRENT` |
| `MAT_MAX_CONCURRENT` | `1` | Parallel MAT runs across all worker processes |
| `MAT_QUEUE_TIMEOUT_SECONDS` | `3600` | Wait for a MAT slot before 503 |
| `MAT_TIMEOUT` | `3600` | Seconds before the MAT process group is killed (504) |
| `HEAPDUMPS_DIR` | `/heapdumps` | Parent of the per-request work directories |
| `MAX_DUMP_SIZE_BYTES` | `68719476736` (64 GB) | Limit after decompressing .hprof.gz |
| `MAX_UPLOAD_SIZE_BYTES` | `21474836480` (20 GB) | Upload file size limit (413 if exceeded) |
| `LOG_LEVEL` | `INFO` | Root log level |
| `LOG_JSON` | `false` | `true` → JSON-structured logging for ELK/CloudWatch |

### Analyzer Thresholds (env var prefixed)

Suspects (`SUSPECTS_` prefix): `MIN_PROBLEM_MB=10`, `MIN_PROBLEM_PCT=10` (smaller suspects are no problems), `PRIMARY_LEAK_HIGH_MB=500`, `SIGNIFICANT_SUSPECT_MB=50`, `SIGNIFICANT_SUSPECT_RATIO=0.2`, `SECONDARY_LEAK_HIGH_MB=200`, `HEAP_LEAK_CRITICAL_PCT=70`, `HEAP_LEAK_WARNING_PCT=40`

Overview (`OVERVIEW_` prefix): `LARGE_HEAP_HIGH_MB=2048`, `LARGE_HEAP_MEDIUM_MB=1024`, `HIGH_OBJECT_COUNT=1000000`, `ELEVATED_OBJECT_COUNT=500000`, `HIGH_CLASSLOADER_COUNT=20`, `ELEVATED_CLASSLOADER_COUNT=10`, `HIGH_GC_ROOT_COUNT=5000`, `THREAD_LEAK_MB=50`, `THREAD_LEAK_SEVERE_MB=100`, `LARGE_ARRAY_MB=100`, `LARGE_STRING_MB=100`, `LARGE_CACHE_MB=100`

Top Components (`TOP_COMPONENTS_` prefix): `DOMINANT_CLASSLOADER_MB=200`, `DOMINANT_CLASSLOADER_HIGH_PCT=50`, `DOMINANT_CONSUMER_MB=500`, `DOMINANT_CONSUMER_PCT=40`, `LARGE_CONSUMER_MB=100`, `WASTE_PROBLEM_MB=50`, `WASTE_WARNING_MB=10`

### Container Paths
- `/opt/eclipse-mat/ParseHeapDump.sh` — MAT executable
- `/opt/eclipse-mat/MemoryAnalyzer.ini` — MAT's own JVM config (`-Xmx1024m`); not effective, see gotcha 2
- `/opt/mat-service/` — Backend code
- `/heapdumps` — Per-request work directories (volume mount)

## Gotchas

1. **MAT is x86-ONLY** — Apple Silicon users MUST add `--platform linux/amd64` to every Docker command. Omitting this silently fails.
2. **MAT heap comes from `-vmargs`, not from `MemoryAnalyzer.ini`** — `-vmargs` on the command line *replaces* the ini's VM options, so `mat_runner.mat_command()` must repeat the ini's `--add-exports`. A mounted ini has no effect. MAT needs ~1.5–2× the dump size.
3. **MAT number formats depend on the JVM locale** — `81.8 MB` vs `81,8 MB`, `>= 84,487,384` in histograms. MAT runs with an English locale; `base._parse_size_to_mb()` still handles both (tests in `test_parsing.py`).
4. **Work directory per request** — never share files between requests: MAT writes index files and ZIPs next to the dump, and `run_all_analyzers()` reads only that directory. It is removed in `finally`.
4a. **MAT HTML structure, not keyword guessing** — suspects: pages titled "Problem Suspect N", path table under "…Accumulation Point" (field in `<strong>`, object in `<a>`, tree prefix `\` / `+`). Top components: index → component pages (`<h4>` check + `<div>` verdict) → "Biggest Objects". Component "Size" values overlap; use the `%` shares.
4b. **MAT pages contain heap String contents** (e.g. values next to references, duplicate-string samples). Results must not carry them; fixtures are scrubbed by `make_fixtures.py`.
5. **File uploads are chunked** — `services/analysis_service.py` reads uploads in 1 MB chunks with a configurable size limit (`MAX_UPLOAD_SIZE_BYTES`, default 20 GB).
6. **Thread-pool offloading** — MAT and analyzer execution run in executor pool (CPU-bound work), awaited by async FastAPI routes. MAT concurrency is limited by file-lock slots in `/heapdumps/.mat-slots` (works across uvicorn worker processes).
7. **Lazy config singletons** — Analyzers call `get_*_thresholds()` internally (not via constructor) to preserve the `analyzer_cls(report_path, out_dir)` call pattern.

## Code Style
- Python: standard library conventions, no formatter configured
- Type hints used in Pydantic models and FastAPI route signatures
- Severity levels: CRITICAL/HIGH, MEDIUM/WARN, LOW, unknown
