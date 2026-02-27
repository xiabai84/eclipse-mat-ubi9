#!/bin/bash
OQL_SCRIPT=$1
HEAPDUMP=$2
OUTPUT_FILE=$3

if [ -z "$OQL_SCRIPT" ] || [ -z "$HEAPDUMP" ]; then
    echo "Usage: oql-analyze.sh <oql_script.oql> <heapdump.hprof> [output.txt]" >&2
    exit 1
fi

if [ ! -f "$OQL_SCRIPT" ]; then
    echo "OQL script not found: $OQL_SCRIPT" >&2
    exit 1
fi

if [ ! -f "$HEAPDUMP" ]; then
    echo "Heap dump not found: $HEAPDUMP" >&2
    exit 1
fi

if [ -z "$OUTPUT_FILE" ]; then
    OUTPUT_FILE="/reports/oql_results_$(date +%Y%m%d_%H%M%S).txt"
fi

echo "Running OQL analysis on $HEAPDUMP..."
/opt/eclipse-mat/ParseHeapDump.sh "$HEAPDUMP"     org.eclipse.mat.api:oql?"$(cat $OQL_SCRIPT)" > "$OUTPUT_FILE" 2>&1

echo "Results saved to: $OUTPUT_FILE"