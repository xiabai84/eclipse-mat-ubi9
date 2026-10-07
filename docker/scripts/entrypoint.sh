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
             --memory=16g -e API_TOKEN=<token> \\
             -p 8080:8080 \\
             eclipse-mat [service]

Commands:
  service   Start the REST analysis service (default)
  --help    Show this help message

REST API (with API_TOKEN set: header "Authorization: Bearer <token>"):
  GET  http://localhost:8080/health                   Liveness probe
  POST http://localhost:8080/analyze/heapdump         Upload .hprof / .hprof.gz -> JSON analysis
  POST http://localhost:8080/analyze/heapdump/report  Upload .hprof / .hprof.gz -> text report
  GET  http://localhost:8080/docs                     OpenAPI / Swagger UI

MAT heap: 75 % of the container memory limit, or MAT_XMX. See README "Configuration".
EOF
        exit 0
        ;;

    *)
        echo "Unknown command: $1" >&2
        echo "Run with --help for usage information." >&2
        exit 1
        ;;
esac
