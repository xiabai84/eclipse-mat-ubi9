# Fix analysis correctness, concurrency and security

Verified against real Eclipse MAT 1.16.1 output (demo scenarios 1, 2, 4, 5 and a ground-truth program with a
known 120 MB cache, a 60 MB ThreadLocal, 200k equal strings, 100k empty maps), not only against the hand-written
HTML fixtures the current tests use.

## Verified defects

### Analysis results (wrong numbers / wrong findings)

| # | Where | Defect | Evidence |
| --- | --- | --- | --- |
| A1 | `base._parse_size_to_mb` | Decimal comma (MAT under a German/European JVM locale) read as thousands separator | `81,8 MB` → 818 MB; leak share 9.8 % instead of 97.8 % |
| A2 | `base._parse_size_to_mb` | `>= 84,487,384` (MAT's retained-size notation in histograms) parses to 0 | every `class_histogram[].retained_mb` is 0.0 → `LARGE_ARRAYS` / `LARGE_STRING_USAGE` can never fire (ground truth: 180 MB `byte[]`, no finding) |
| A3 | `overview._parse_histogram`, `top_components` | Link text glued to names | `byte[]All objects`, `java.util.ArrayListAll objects`, `All 5 objects`, `+ JavaMemoryIssuesDemo Only object` |
| A4 | `top_components` | MAT's tree levels (class loader → class → object) each counted as a consumer | 5 × `HIGH DOMINANT_CONSUMER` for the same 57.7 MB; `classloaders` always empty |
| A5 | `top_components` waste | Sums table columns instead of reading MAT's verdict; reads only 3 of MAT's 6 waste checks | ground truth: MAT reports "Collection Fill Ratios … ThreadLocalMap retain 62,914,712 bytes" – analyzer reports nothing |
| A6 | `suspects` | No minimum size: on a healthy 2 MB heap MAT always names suspects, analyzer turns them into problems | `PRIMARY_LEAK: java.lang.Class retains 0.6 MB` (MEDIUM) |
| A7 | `suspects.key_objects` | Shows *shallow* size of the path entries, with tree glyphs | `java.lang.Object[109] @ 0x…: 456` (actual retained 83,889,736) |
| A8 | `suspects` | Accumulation point and the field holding the memory not extracted | MAT: "accumulated in one instance of java.lang.Object[]" via `STATIC_SESSIONS` – the key information is missing |
| A9 | `base._clean_text` | Unescapes before stripping tags → `<system class loader>` deleted | description "loaded by , which occupies …" |
| A10 | `suspects` thread | Regex picks any `Thread @ 0x…` on the page (the GC-root thread of the path) and any prose after "thread" | suspect 2 of scenario 4 gets thread `main` |
| A11 | `overview` | Object count > 1 M is a HIGH problem, GC roots > 5000 MEDIUM – true for almost every production heap | noise, not a finding |

### Service (concurrency, failures, resources)

| # | Defect |
| --- | --- |
| S1 | 4 uvicorn workers share `/heapdumps` and `/reports`: every run moves *all* ZIPs and deletes *all* index files; analyzers take the *first* matching ZIP → results swapped, missing or stale under concurrent requests |
| S2 | MAT non-zero exit is only a "warning"; the pipeline then analyses whatever old ZIP is in `/reports` |
| S3 | On MAT error/timeout the uploaded dump is never deleted (exception before cleanup); `/analyze/*` temp dirs never removed |
| S4 | MAT heap never configured (`MemoryAnalyzer.ini` default), docs claim `-Xmx32g`; up to 4 MAT JVMs in a 16 Gi pod |
| S5 | Timeout kills the shell script only, the MAT JVM keeps running |
| S6 | `.hprof.gz` rejected (jvm-diag produces `.hprof.gz` by default) |
| S7 | Size limit checked only while copying, after Starlette has spooled the whole upload to `/tmp` (emptyDir without `sizeLimit`) |

### Security

| # | Defect |
| --- | --- |
| X1 | No authentication on any endpoint; Route enabled by default (20 GB body, 1800 s) |
| X2 | Caller-controlled server paths: `heapdumps_dir` / `reports_dir` form fields, `report_path` / `output_dir` / `reports_dir` in JSON and query → list, move and delete files anywhere the pod can write |
| X3 | Responses expose server paths and MAT stdout/stderr |
| X4 | Nothing pinned: base images `:latest`, pip `>=`, MAT download without checksum, Helm image tag `latest` |

## Plan

- [x] **Isolated work directory per request** (`/heapdumps/<uuid>/`): dump, MAT index files and report ZIPs stay
      there; analyzers read only that directory; always removed in `finally` (S1, S2, S3)
- [x] **MAT concurrency limit across worker processes** (file-lock slots, `MAT_MAX_CONCURRENT`, default 1; `503`
      with `Retry-After` when the queue wait exceeds `MAT_QUEUE_TIMEOUT`) (S1, S4)
- [x] **MAT failure = error**: non-zero exit or missing report → `502` with a short message, no stale data (S2)
- [x] **MAT JVM options**: `MAT_XMX` (default from the cgroup memory limit ÷ concurrency, 75 %) and
      `-Duser.language=en -Duser.country=US` via `-vmargs` (S4, A1)
- [x] **Timeout kills the process group** (S5)
- [x] **Accept `.hprof.gz`**, decompress into the work directory (S6)
- [x] **Reject oversized uploads early** from `Content-Length`; Helm: `sizeLimit` on `/tmp` (S7)
- [x] **Optional Bearer token auth** (`API_TOKEN`, from a Secret in Helm) on everything except `/health`; without a
      token the service logs a warning at startup (X1)
- [x] **No caller-controlled paths**: remove the dir form fields and the path-based endpoints (X2)
- [x] **No internals in responses**: no `saved_to`, MAT output only as a short error excerpt (X3)
- [x] **Pin** base images (tags), Python dependencies (exact versions), MAT download (SHA-512), Helm image tag from
      `Chart.appVersion` (X4)
- [x] **Analyzer fixes** A1–A11; JSON keeps its shape, adds `accumulation_point` and `path` to suspects, `components`
      and all six MAT waste checks to top components
- [x] **Tests against real MAT output**: CI job that downloads MAT (checksum), runs the demo and a ground-truth
      program, and asserts the parsed numbers; unit-test fixtures replaced by excerpts of real MAT HTML (no system
      properties, no local paths)
- [x] Docs: README, CLAUDE.md (`-Xmx` claim), Helm values; version 4.0.0 (auth is a breaking change)

## Decisions (2026-10-07)

1. Auth **optional**: enforced when `API_TOKEN` is set; without it the service stays open and logs a warning.
2. Path-based endpoints **removed**: `/analyze/suspects`, `/analyze/overview`, `/analyze/top-components`,
   `/analyze/all`, `/reports`. Remaining: `POST /analyze/heapdump`, `POST /analyze/heapdump/report`, `GET /health`.
3. Suspects below 10 MB retained and below 10 % of the heap are not problems; object count and GC roots become
   warnings.

Version 4.0.0 (removed endpoints and form fields are breaking changes).

## Review (2026-10-07)

All plan items done. Verification:

- **Real MAT output.** MAT 1.16.1 (same zip as the Dockerfile, SHA-512 confirmed against Eclipse's published value)
  run headless on demo scenarios 1/2/4/5 and `tests/integration/GroundTruth.java`; every analyzer number compared
  with MAT's own pages. Fixtures in `backend/tests/fixtures/mat/` are reduced, scrubbed copies (no local paths,
  user or host names - one real leak was caught: a `USER_NAME` String value on a MAT page).
- **Tests:** 63 unit tests (real-MAT fixtures, fake MAT for the service) + 2 integration tests with real MAT
  (run locally through a macOS launcher stand-in; in CI with the real Linux launcher). Mutation-checked: killing
  only the MAT script instead of its process group, and a shared work directory, both make tests fail.
- **Not verified locally:** the Docker image (the local Docker VM cannot emulate amd64). The CI job `container`
  builds it and smoke-tests it (3 GiB limit → `-Xmx2304m`, token, ground-truth dump, empty work directory).

Deviations from the decisions, to confirm:

- Decision 3 says "below 10 MB *and* below 10 % → no problem". Implemented as "below 10 MB *or* below 10 %":
  on the 2 MB demo heap the false suspect is 0.6 MB but 31.9 % of the heap, so the literal rule would keep the false
  positive. `SUSPECTS_MIN_PROBLEM_PCT=0` gives a pure size limit.
- `DOMINANT_CLASSLOADER` became a warning (like object count / GC roots): application classes always live in the
  application class loader, so it fired on every heap.
- The `/reports` PVC was removed from the chart (nothing writes there any more).
