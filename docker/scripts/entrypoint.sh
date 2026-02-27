#!/bin/bash
# Eclipse MAT container entrypoint
# Starts the Python REST service for heap dump analysis.

set -e

case "$1" in
    service)
        echo "Starting MAT Analysis REST Service on port 8080..."
        exec uvicorn app:app \
            --app-dir /opt/mat-service \
            --host 0.0.0.0 \
            --port 8080 \
            --workers "${UVICORN_WORKERS:-4}" \
            --log-level info
        ;;

    --help|-h|"")
        cat <<EOF
Eclipse MAT Heap Analysis REST Service

Usage:
  docker run -v \$(pwd)/heapdumps:/heapdumps \\
             -v \$(pwd)/reports:/reports \\
             -p 8080:8080 \\
             eclipse-mat [service]

Commands:
  service   Start the REST analysis service (default)
  --help    Show this help message

REST API:
  GET  http://localhost:8080/health              Liveness probe
  GET  http://localhost:8080/reports             List ZIP reports
  POST http://localhost:8080/analyze/heapdump    Upload .hprof -> JSON analysis
  POST http://localhost:8080/analyze/heapdump/report  Upload .hprof -> text report
  POST http://localhost:8080/analyze/suspects    Analyse Leak Suspects ZIP
  POST http://localhost:8080/analyze/overview    Analyse System Overview ZIP
  POST http://localhost:8080/analyze/top-components  Analyse Top Components ZIP
  POST http://localhost:8080/analyze/all         Auto-discover & run all analysers
  GET  http://localhost:8080/docs                OpenAPI / Swagger UI
EOF
        exit 0
        ;;

    *)
        echo "Unknown command: $1" >&2
        echo "Run with --help for usage information." >&2
        exit 1
        ;;
esac
