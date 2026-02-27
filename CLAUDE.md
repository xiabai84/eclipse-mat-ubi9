## Workflow Orchestration

### 1. Plan Node Default
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
  -p 8080:8080 \
  -v $(pwd)/heapdumps:/heapdumps \
  -v $(pwd)/reports:/reports \
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

### Analyze Heap Dumps
```bash
# JSON response
curl -s -X POST http://localhost:8080/analyze/heapdump -F "file=@./heapdumps/myapp.hprof"

# Human-readable text report
curl -s -X POST http://localhost:8080/analyze/heapdump/report -F "file=@./heapdumps/myapp.hprof"
```

### Run Analyzers Locally (no Docker)
```bash
pip install -r backend/requirements.txt
python3 backend/mat_suspect_analyzer.py ./reports/myapp_Leak_Suspects.zip
```

## Architecture

```
backend/
├── app.py                    # FastAPI application (all routes, MAT subprocess runner)
├── requirements.txt          # Python dependencies
├── mat_suspect_analyzer.py   # CLI shim (backward compat)
├── mat_system_overview_analyzer.py
├── mat_top_components_analyzer.py
└── analyzers/
    ├── __init__.py           # Exports three analyzer classes
    ├── base.py               # MATBaseAnalyzer (abstract): ZIP extraction, HTML parsing, Java recommendations engine
    ├── suspects.py           # MATLeakSuspectsAnalyzer: leak suspect objects, heap %, retained sizes
    ├── overview.py           # MATSystemOverviewAnalyzer: heap summary, top entries by type
    └── top_components.py     # MATTopComponentsAnalyzer: largest retained-heap components

docker/
├── Dockerfile                # 3-stage build: rpm-builder → pip-builder → runtime
└── scripts/
    ├── entrypoint.sh         # Command dispatcher (service|analyze|oql|shell|...)
    ├── analyze-heapdump.sh   # Shell wrapper for MAT ParseHeapDump.sh
    └── unpackRPM.sh          # RPM extraction for multi-stage

demo/
├── src/JavaMemoryIssuesDemo.java  # 7 memory leak scenarios for testing
└── run-demo.sh                    # Compile & run automation
```

### Data Flow
```
Upload .hprof → Save to /heapdumps → Run MAT (subprocess) → Generate ZIPs in /reports
  → Parse ZIPs with BeautifulSoup → Extract tables/data → Build report → Return JSON or text
```

### Analyzer Inheritance Pattern
- `MATBaseAnalyzer` (abstract): ZIP extraction, HTML parsing, formatting utilities, Java recommendations DB
- Subclasses implement `parse_report()` and `generate_report()`
- `build_summary()` matches detected problems against 10+ diagnostic patterns (PRIMARY_LEAK, LARGE_HEAP, THREAD_LEAK, etc.)

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness probe |
| GET | `/reports` | List ZIP reports in /reports |
| POST | `/analyze/heapdump` | Upload .hprof → JSON analysis |
| POST | `/analyze/heapdump/report` | Upload .hprof → human-readable text |
| POST | `/analyze/suspects` | Analyze Leak Suspects ZIP |
| POST | `/analyze/overview` | Analyze System Overview ZIP |
| POST | `/analyze/top-components` | Analyze Top Components ZIP |
| POST | `/analyze/all` | Auto-discover & run all three analyzers |

## Environment & Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `MAT_TIMEOUT` | `600` | Seconds before MAT subprocess is killed |

### Container Paths
- `/opt/eclipse-mat/ParseHeapDump.sh` — MAT executable
- `/opt/eclipse-mat/MemoryAnalyzer.ini` — JVM config (`-Xmx32g -Xms4g`)
- `/opt/mat-service/` — Backend code
- `/heapdumps` — Uploaded .hprof files (volume mount)
- `/reports` — Generated ZIP reports (volume mount)

## Gotchas

1. **MAT is x86-ONLY** — Apple Silicon users MUST add `--platform linux/amd64` to every Docker command. Omitting this silently fails.
2. **MAT JVM heap must be >= 2x dump size** — For a 10 GB heap dump, MAT needs 20+ GB. Default `-Xmx32g` covers dumps up to ~16 GB. Override by mounting a custom `MemoryAnalyzer.ini`.
3. **European locale numbers in MAT HTML** — MAT may emit `"1.234,56 %"` instead of `"1234.56 %"`. Parsers in `suspects.py` handle both formats with fallback logic.
4. **ZIP cleanup after analysis** — `app.py` deletes generated MAT ZIPs after analysis to prevent filling `/reports`. This is intentional.
5. **File uploads are chunked** — `app.py` reads uploads in 1 MB chunks to avoid buffering entire dumps in memory.
6. **Thread-pool offloading** — MAT and analyzer execution run in executor pool (CPU-bound work), awaited by async FastAPI routes.

## Code Style
- Python: standard library conventions, no formatter configured
- Type hints used in Pydantic models and FastAPI route signatures
- Severity levels: CRITICAL/HIGH, MEDIUM/WARN, LOW, unknown
