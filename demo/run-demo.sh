#!/bin/bash

# =============================================================================
# run-demo.sh — Compile and run JavaMemoryIssuesDemo, capture heap dumps
# =============================================================================
# Usage:
#   ./run-demo.sh              # interactive menu
#   ./run-demo.sh all          # run ALL 7 scenarios → one dedicated .hprof each
#   ./run-demo.sh 1            # run scenario 1 only → dedicated .hprof
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# demo/ lives one level below the project root
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
HEAPDUMP_DIR="$PROJECT_DIR/heapdumps"
JAVA_FILE="$SCRIPT_DIR/src/JavaMemoryIssuesDemo.java"
CLASS_DIR="$SCRIPT_DIR/target/classes"
MODE="${1:-menu}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${GREEN}[run-demo]${NC} $*"; }
warn() { echo -e "${YELLOW}[run-demo]${NC} $*"; }
err()  { echo -e "${RED}[run-demo]${NC} $*" >&2; }
info() { echo -e "${CYAN}[run-demo]${NC} $*"; }

# -----------------------------------------------------------------------------
# Scenario registry — number → slug used for the output filename
# Slugs mirror the exact names from the JavaMemoryIssuesDemo menu/javadoc.
# -----------------------------------------------------------------------------
declare -A SCENARIO_SLUG=(
    [1]="01_static_collection_leak"
    [2]="02_cache_without_eviction"
    [3]="03_event_listener_leak"
    [4]="04_threadlocal_leak"
    [5]="05_string_duplication"
    [6]="06_classloader_resource_leak"
    [7]="07_large_object_allocation"
)

declare -A SCENARIO_LABEL=(
    [1]="Static Collection Leak"
    [2]="Cache Without Eviction"
    [3]="Event-Listener Leak"
    [4]="ThreadLocal Leak"
    [5]="String Duplication"
    [6]="ClassLoader / Resource Leak"
    [7]="Large Object Allocation"
)

# -----------------------------------------------------------------------------
# Pre-flight checks
# -----------------------------------------------------------------------------
if ! command -v javac &>/dev/null; then
    err "javac not found. Install Java 17+ (e.g. brew install openjdk@17)."
    exit 1
fi

JAVA_VERSION_RAW=$(java -version 2>&1 | head -1 | sed 's/.*"\([^"]*\)".*/\1/')
JAVA_MAJOR=$(echo "$JAVA_VERSION_RAW" | cut -d. -f1)
if [[ "$JAVA_MAJOR" == "1" ]]; then
    JAVA_MAJOR=$(echo "$JAVA_VERSION_RAW" | cut -d. -f2)
fi
if [[ -z "$JAVA_MAJOR" ]] || [[ "$JAVA_MAJOR" -lt 11 ]]; then
    err "Java 11+ required (found: ${JAVA_VERSION_RAW:-unknown})."
    exit 1
fi
log "Java $JAVA_MAJOR detected (${JAVA_VERSION_RAW})"

if [[ ! -f "$JAVA_FILE" ]]; then
    err "JavaMemoryIssuesDemo.java not found: $JAVA_FILE"
    exit 1
fi

# -----------------------------------------------------------------------------
# Compile
# -----------------------------------------------------------------------------
info "Compiling JavaMemoryIssuesDemo.java …"
mkdir -p "$CLASS_DIR"
if ! javac -d "$CLASS_DIR" "$JAVA_FILE"; then
    err "Compilation failed."
    exit 1
fi
log "Compilation OK"

# -----------------------------------------------------------------------------
# Prepare heap dump directory
# -----------------------------------------------------------------------------
mkdir -p "$HEAPDUMP_DIR"
info "Heap dumps → $HEAPDUMP_DIR"
echo ""

# -----------------------------------------------------------------------------
# run_scenario <n>
#   Runs scenario N in its own fresh JVM process and produces a dedicated
#   heap dump at:  heapdumps/scenario_<slug>.hprof
#
# JVM flags:
#   -Xms256m / -Xmx512m          small heap so OOM arrives quickly in scenario 7
#   -XX:+HeapDumpOnOutOfMemoryError  auto-dump on OOM (covers scenario 7)
#   -XX:HeapDumpPath=<file>       OOM dump goes to the scenario-specific file
#   3rd Java arg                  explicit dump path for scenarios 1-6
# -----------------------------------------------------------------------------
run_scenario() {
    local n="$1"
    local slug="${SCENARIO_SLUG[$n]}"
    local label="${SCENARIO_LABEL[$n]}"
    local dump_file="$HEAPDUMP_DIR/scenario_${slug}.hprof"

    info "  [$n/7] $label"

    # Remove stale dump from a previous run so we can reliably detect success
    rm -f "$dump_file"

    java \
        -Xms256m \
        -Xmx512m \
        -XX:+HeapDumpOnOutOfMemoryError \
        "-XX:HeapDumpPath=$dump_file" \
        -cp "$CLASS_DIR" \
        JavaMemoryIssuesDemo "$n" "$HEAPDUMP_DIR" "$dump_file"

    local rc=$?

    if [[ -f "$dump_file" ]]; then
        local size
        size=$(du -sh "$dump_file" 2>/dev/null | cut -f1)
        log "    ${GREEN}✓${NC}  scenario_${slug}.hprof  (${size})"
    else
        if [[ $n -eq 7 && $rc -ne 0 ]]; then
            warn "    Scenario 7 OOM dump not found — heap may have been too small for the dump write"
        else
            warn "    No dump produced for scenario $n (exit code $rc)"
        fi
    fi

    return $rc
}

# -----------------------------------------------------------------------------
# Mode dispatch
# -----------------------------------------------------------------------------
case "$MODE" in

    # ── all: run every scenario in its own JVM ────────────────────────────
    all)
        log "Running ALL 7 scenarios — each in a dedicated JVM process …"
        echo ""
        PASS=0; FAIL=0
        for n in 1 2 3 4 5 6 7; do
            run_scenario "$n"
            rc=$?
            # Non-zero exit is expected for scenario 7 (OOM); count it as pass
            if [[ $rc -eq 0 || $n -eq 7 ]]; then
                ((PASS++))
            else
                ((FAIL++))
            fi
            echo ""
        done

        echo ""
        log "Results: ${PASS}/7 scenarios completed${FAIL:+ — ${FAIL} failed}"
        ;;

    # ── single scenario by number ─────────────────────────────────────────
    [1-7])
        n="$MODE"
        log "Running scenario $n: ${SCENARIO_LABEL[$n]} …"
        echo ""
        run_scenario "$n"
        ;;

    # ── interactive menu (unchanged behaviour) ────────────────────────────
    menu)
        log "Starting JavaMemoryIssuesDemo in interactive menu mode …"
        echo ""
        java \
            -Xms256m \
            -Xmx512m \
            -XX:+HeapDumpOnOutOfMemoryError \
            "-XX:HeapDumpPath=$HEAPDUMP_DIR/oom_dump.hprof" \
            -cp "$CLASS_DIR" \
            JavaMemoryIssuesDemo menu "$HEAPDUMP_DIR"
        ;;

    *)
        err "Unknown mode: '$MODE'"
        echo "Usage: $0 [all | 1-7 | menu]"
        exit 1
        ;;
esac

# -----------------------------------------------------------------------------
# Summary of all .hprof files present
# -----------------------------------------------------------------------------
echo ""
log "Heap dumps in $HEAPDUMP_DIR:"
if ls "$HEAPDUMP_DIR"/*.hprof 1>/dev/null 2>&1; then
    for f in "$HEAPDUMP_DIR"/*.hprof; do
        SIZE=$(du -sh "$f" 2>/dev/null | cut -f1)
        echo -e "    ${GREEN}✓${NC}  ${SIZE}   $(basename "$f")"
    done
else
    warn "No .hprof files found — check that the scenario ran to completion."
fi

echo ""
log "Next step — analyse with the running service:"
echo "    # Single file:"
echo "    curl -s -X POST http://localhost:8080/analyze/heapdump/report \\"
echo "         -F \"file=@$HEAPDUMP_DIR/scenario_01_static_collection_leak.hprof\""
echo ""
echo "    # All files at once (pre-generated ZIPs workflow):"
echo "    curl -X POST http://localhost:8080/analyze/all \\"
echo "         -H 'Content-Type: application/json' \\"
echo "         -d '{\"reports_dir\": \"/reports\"}'"
echo ""
