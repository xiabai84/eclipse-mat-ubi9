#!/bin/bash
HEAPDUMP=$1
REPORT_TYPE=$2
REPORT_DIR=/reports

if [ -z "$HEAPDUMP" ]; then
    echo "Usage: analyze-heapdump.sh <heapdump.hprof> [report-type]" >&2
    echo "Report types: leak_suspects, overview, top_components, all" >&2
    exit 1
fi

if [ ! -f "$HEAPDUMP" ]; then
    echo "Heap dump not found: $HEAPDUMP" >&2
    exit 1
fi

echo "=================================================="
echo "MAT Heap Dump Analysis - $(date)"
echo "Heap dump: $HEAPDUMP"
echo "Report type: ${REPORT_TYPE:-all}"
echo "=================================================="

if [ "$REPORT_TYPE" = "leak_suspects" ]; then
    echo "Generating leak suspects report..."
    /opt/eclipse-mat/ParseHeapDump.sh "$HEAPDUMP" org.eclipse.mat.api:suspects
    echo "Report generated: $(basename "$HEAPDUMP")_Leak_Suspects.zip"
    mv $HEAPDUMP/*.zip $REPORT_DIR/ 2>/dev/null || true
    report_script.sh $REPORT_DIR/*Leak_Suspects.zip $REPORT_DIR/output.txt 2>/dev/null || true
    
elif [ "$REPORT_TYPE" = "overview" ]; then
    echo "Generating overview report..."
    /opt/eclipse-mat/ParseHeapDump.sh "$HEAPDUMP" org.eclipse.mat.api:overview
    echo "Report generated: $(basename "$HEAPDUMP")_System_Overview.zip"
    mv $HEAPDUMP/*.zip $REPORT_DIR/ 2>/dev/null || true
    
elif [ "$REPORT_TYPE" = "top_components" ]; then
    echo "Generating top components report..."
    /opt/eclipse-mat/ParseHeapDump.sh "$HEAPDUMP" org.eclipse.mat.api:top_components
    echo "Report generated: $(basename "$HEAPDUMP")_Top_Components.zip"
    mv $HEAPDUMP/*.zip $REPORT_DIR/ 2>/dev/null || true
    
else
    echo "Generating all reports..."
    /opt/eclipse-mat/ParseHeapDump.sh "$HEAPDUMP"         org.eclipse.mat.api:overview         org.eclipse.mat.api:suspects         org.eclipse.mat.api:top_components
    echo "Reports generated:"
    ls -l $(dirname $(realpath $HEAPDUMP))/*.zip
    ls -l $(dirname $(realpath $HEAPDUMP))
    mv $(dirname $(realpath $HEAPDUMP))/*.zip $REPORT_DIR/ 2>/dev/null || true
    report_script.sh $REPORT_DIR/*Leak_Suspects.zip $REPORT_DIR/output.txt 2>/dev/null || true
fi

echo "Cleanup directory$HEAPDUMP..."
rm -f /heapdumps/*.index /heapdumps/*.threads /heapdumps/*.o2c* /heapdumps/*.inbound* /heapdumps/*.outbound* /heapdumps/*.array* /heapdumps/*.i2sv2*

echo "=================================================="
echo "Analysis complete! Reports saved to $REPORT_DIR/"
ls -la $REPORT_DIR/ 2>/dev/null || echo "No reports generated"