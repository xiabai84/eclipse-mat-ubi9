#!/bin/bash
# Eclipse MAT container entrypoint
# Dispatches to MAT analysis commands or starts the Python REST service.

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

    oql)
        shift
        exec oql-analyze.sh "$@"
        ;;

    analyze)
        shift
        exec analyze-heapdump.sh "$@"
        ;;

    parse)
        shift
        exec /opt/eclipse-mat/ParseHeapDump.sh "$@"
        ;;

    shell)
        exec /bin/bash
        ;;

    py-suspects)
        # Run the suspects analyzer directly (convenience shortcut)
        shift
        exec python3 /opt/mat-service/mat_suspect_analyzer.py "$@"
        ;;

    py-overview)
        shift
        exec python3 /opt/mat-service/mat_system_overview_analyzer.py "$@"
        ;;

    py-top-components)
        shift
        exec python3 /opt/mat-service/mat_top_components_analyzer.py "$@"
        ;;

    --help|-h|"")
        cat <<EOF
Eclipse MAT Headless Analyzer + Python Analysis Service

Usage:
  docker run -v \$(pwd)/heapdumps:/heapdumps:ro \\
             -v \$(pwd)/reports:/reports \\
             [-p 8080:8080] \\
             eclipse-mat <command>

Commands:
  service                          Start the Python REST analysis service (default)
  analyze <dump.hprof> [type]      Generate MAT reports via ParseHeapDump.sh
                                   Types: leak_suspects | overview | top_components | all
  oql <script.oql> <dump.hprof>   Run an OQL query against a heap dump
  parse <dump.hprof> <report>      Raw ParseHeapDump.sh pass-through
  shell                            Open a bash shell

  py-suspects   <report.zip>       Run standalone Leak Suspects analyser
  py-overview   <report.zip>       Run standalone System Overview analyser
  py-top-components <report.zip>   Run standalone Top Components analyser

REST API (when running 'service'):
  GET  http://localhost:8080/health
  GET  http://localhost:8080/reports
  POST http://localhost:8080/analyze/suspects
  POST http://localhost:8080/analyze/overview
  POST http://localhost:8080/analyze/top-components
  POST http://localhost:8080/analyze/all
  GET  http://localhost:8080/docs     (OpenAPI / Swagger UI)

Example — full workflow:
  # 1. Generate MAT reports from a heap dump
  docker run -v \$(pwd)/heapdumps:/heapdumps:ro \\
             -v \$(pwd)/reports:/reports \\
             eclipse-mat analyze /heapdumps/app.hprof all

  # 2. Start the service and query the reports
  docker run -p 8080:8080 \\
             -v \$(pwd)/reports:/reports \\
             eclipse-mat service

  curl -X POST http://localhost:8080/analyze/all \\
       -H "Content-Type: application/json" \\
       -d '{"reports_dir": "/reports"}'
EOF
        exit 0
        ;;

    *)
        echo "Unknown command: $1" >&2
        echo "Run with --help for usage information." >&2
        exit 1
        ;;
esac
