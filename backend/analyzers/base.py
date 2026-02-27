#!/usr/bin/env python3
"""
Base class for all MAT (Eclipse Memory Analyzer Tool) report analyzers.

Provides shared utilities for HTML parsing, size conversions, file I/O,
visual report formatting, and a Java-specific recommendation engine.
"""

import html
import json
import logging
import re
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_W = 80   # default report width


# ── Visual formatting helpers ────────────────────────────────────────────────

def _bar(pct: float, width: int = 20) -> str:
    """Return a filled progress bar:  [████████░░░░] 80.0%"""
    filled = min(int(round(pct / 100 * width)), width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct:.1f}%"


def _section(title: str, width: int = _W) -> str:
    bar = f"── {title} "
    return bar + "─" * max(0, width - len(bar))


def _banner(title: str, subtitle: str = "", width: int = _W) -> str:
    lines = [f"╔{'═' * (width - 2)}╗"]
    for text in ([title] + ([subtitle] if subtitle else [])):
        t = f"  {text}"
        lines.append(f"║{t}{' ' * (width - 2 - len(t))}║")
    lines.append(f"╚{'═' * (width - 2)}╝")
    return "\n".join(lines)


def _severity_icon(severity: str) -> str:
    return {"CRITICAL": "🔴", "HIGH": "🔴", "MEDIUM": "🟡",
            "LOW": "🔵", "WARN": "🟡"}.get(severity.upper(), "⚪")


# ── Java recommendation database ─────────────────────────────────────────────

JAVA_RECOMMENDATIONS: Dict[str, Dict[str, Any]] = {

    "PRIMARY_LEAK": {
        "title": "Memory Leak — Primary Suspect",
        "root_cause": (
            "A single object retains an unexpectedly large portion of the heap. "
            "Typically a long-lived container (static field, singleton, cache, or thread-local) "
            "holds strong references that were never released."
        ),
        "fix_steps": [
            "Identify the GC root object in the Leak Suspects report.",
            "Use MAT 'Path to GC Roots' to trace the reference chain.",
            "Add explicit cleanup — clear(), remove(), close() — at the lifecycle boundary.",
            "Use WeakReference / SoftReference for optional cached data.",
            "Replace HashMap with Caffeine / Guava Cache (bounded, evicting).",
        ],
        "code_example": """\
// BAD: static map grows forever
private static final Map<String, Session> SESSIONS = new HashMap<>();

// GOOD: Caffeine cache with eviction
private static final Cache<String, Session> SESSIONS =
    Caffeine.newBuilder()
        .maximumSize(10_000)
        .expireAfterAccess(30, TimeUnit.MINUTES)
        .build();""",
        "references": [
            "https://docs.oracle.com/javase/8/docs/technotes/guides/troubleshoot/memleaks002.html",
            "https://github.com/ben-manes/caffeine",
        ],
    },

    "SIGNIFICANT_LEAK_RATIO": {
        "title": "Heap Dominated by Leak Suspects (>50%)",
        "root_cause": (
            "Leak suspects account for more than half the heap — a systemic leak "
            "rather than an isolated incident."
        ),
        "fix_steps": [
            "Take heap dumps at intervals; compare with MAT 'Compare Snapshots'.",
            "Find classes whose instance count grows monotonically over time.",
            "Profile allocation hot-spots with async-profiler or JFR.",
            "Audit all AutoCloseable resources for try-with-resources usage.",
        ],
        "code_example": """\
// GOOD: always use try-with-resources
try (Connection conn = dataSource.getConnection();
     PreparedStatement ps = conn.prepareStatement(sql)) {
    // work
}  // both closed automatically on exit or exception""",
        "references": [
            "https://docs.oracle.com/javase/tutorial/essential/exceptions/tryResourceClose.html",
        ],
    },

    "LARGE_HEAP": {
        "title": "Unexpectedly Large Heap Usage (>1 GB)",
        "root_cause": (
            "The JVM heap is very large. May be normal for the workload, or may indicate "
            "objects are not released as expected."
        ),
        "fix_steps": [
            "Review GC logs: -Xlog:gc* and inspect pause times.",
            "Verify -Xmx is appropriate — do not set it higher than necessary.",
            "Use G1GC (default in JDK 9+) or ZGC for large heaps.",
            "Enable string deduplication: -XX:+UseStringDeduplication.",
            "Review cache sizes and enforce explicit upper bounds.",
        ],
        "code_example": """\
# Recommended JVM flags for large-heap services
java -Xms2g -Xmx8g \\
     -XX:+UseG1GC -XX:MaxGCPauseMillis=200 \\
     -XX:+UseStringDeduplication \\
     -Xlog:gc*:file=gc.log:time,uptime:filecount=5,filesize=20m \\
     -jar myapp.jar""",
        "references": ["https://docs.oracle.com/en/java/javase/17/gctuning/"],
    },

    "HIGH_OBJECT_COUNT": {
        "title": "Excessive Object Count (>1 million objects)",
        "root_cause": (
            "Over 1 million heap objects. Typical causes: unbounded caches, "
            "boxing of primitive types (Integer, Long, String), DTO explosion, "
            "or loading entire DB result sets into memory."
        ),
        "fix_steps": [
            "Use primitive-specialised collections (Eclipse Collections, Koloboke).",
            "Apply Flyweight pattern to share immutable state.",
            "Page or stream large DB result sets — never load all rows at once.",
            "Reduce DTO mapping: avoid creating a new object per mapping call.",
        ],
        "code_example": """\
// BAD: autoboxing creates Integer objects
Map<Integer, Integer> map = new HashMap<>();

// GOOD: primitive map (Eclipse Collections)
MutableIntIntMap map = IntIntMaps.mutable.empty();""",
        "references": ["https://eclipse.dev/collections/"],
    },

    "HIGH_CLASSLOADER_COUNT": {
        "title": "ClassLoader Leak (>20 ClassLoaders)",
        "root_cause": (
            "High ClassLoader count indicates a classloader leak — typically caused by "
            "hot-redeploys where old ClassLoaders are not released because a static field "
            "or thread-local still references them."
        ),
        "fix_steps": [
            "After each redeploy, capture a heap dump and check ClassLoader references.",
            "Audit static fields — they keep their ClassLoader alive for the JVM lifetime.",
            "Stop threads and clear thread-locals before undeploy.",
            "Implement ServletContextListener.contextDestroyed() to clean up.",
        ],
        "code_example": """\
@WebListener
public class AppCleanup implements ServletContextListener {
    @Override
    public void contextDestroyed(ServletContextEvent e) {
        Introspector.flushCaches();           // clear reflection caches
        LogManager.shutdown();               // flush log buffers
        AbandonedConnectionCleanupThread.checkedShutdown(); // MySQL driver
    }
}""",
        "references": ["https://wiki.eclipse.org/MemoryAnalyzer/Classloader_Leaks"],
    },

    "HIGH_GC_ROOT_COUNT": {
        "title": "High GC Root Count (>5000 roots)",
        "root_cause": (
            "GC roots (static fields, thread stacks, JNI globals) are the reference "
            "graph's starting points. A large count means more GC work and longer pause times."
        ),
        "fix_steps": [
            "Audit static fields that hold large collections.",
            "Prefer instance fields over static fields.",
            "Reduce long-lived threads; use thread pools with bounded queues.",
            "Audit JNI usage — global JNI references cannot be auto-released by the JVM.",
        ],
        "code_example": """\
// BAD: static registry anchors all objects as GC roots
public class Registry {
    private static final List<Handler> HANDLERS = new ArrayList<>();
}

// GOOD: weak list — handlers GC'd when no longer referenced elsewhere
private static final List<WeakReference<Handler>> HANDLERS =
    new CopyOnWriteArrayList<>();""",
        "references": [],
    },

    "THREAD_LEAK": {
        "title": "Thread / ThreadLocal Memory Leak",
        "root_cause": (
            "Thread-pool threads accumulate data in ThreadLocal variables. "
            "Because pooled threads are never destroyed, the ThreadLocal value "
            "lives for the entire JVM lifetime."
        ),
        "fix_steps": [
            "Always call ThreadLocal.remove() in a finally block after each task.",
            "Pass context via method parameters or request-scoped beans instead.",
            "In Spring Boot: verify RequestContextHolder is reset between requests.",
            "Monitor retained heap per thread in MAT Thread Overview.",
        ],
        "code_example": """\
// BAD: ThreadLocal not removed — leaks in thread pools
executor.submit(() -> {
    CTX.set(new Context(request));
    process();   // CTX.remove() missing!
});

// GOOD: always use try/finally
executor.submit(() -> {
    CTX.set(new Context(request));
    try { process(); }
    finally { CTX.remove(); }   // critical
});""",
        "references": ["https://errorprone.info/bugpattern/ThreadLocalUsage"],
    },

    "LARGE_ARRAYS": {
        "title": "Large Array Allocation",
        "root_cause": (
            "Large arrays (byte[], char[], int[]) retain significant heap. Common causes: "
            "reading entire files into memory, unbound byte buffers, or oversized pre-allocated arrays."
        ),
        "fix_steps": [
            "Stream large files: Files.lines(), InputStream chunking.",
            "Use ByteBuffer.allocateDirect() for I/O-intensive paths (off-heap).",
            "Pool and reuse byte arrays (Netty PooledByteBufAllocator).",
            "Set maximum read sizes and reject oversized inputs early.",
        ],
        "code_example": """\
// BAD: entire file in memory
byte[] data = Files.readAllBytes(largePath);

// GOOD: chunked streaming
try (InputStream in = Files.newInputStream(largePath)) {
    byte[] buf = new byte[8192];
    int n;
    while ((n = in.read(buf)) != -1) process(buf, 0, n);
}""",
        "references": [],
    },

    "LARGE_STRING_USAGE": {
        "title": "Excessive String Memory Usage",
        "root_cause": (
            "Large heap area occupied by String / char[] objects. Common causes: "
            "duplicate strings, log buffers, SQL query strings, or JSON payloads "
            "materialised as strings instead of streamed."
        ),
        "fix_steps": [
            "Enable JVM string deduplication: -XX:+UseStringDeduplication (G1GC only).",
            "Use StringBuilder for string concatenation in loops.",
            "Stream JSON/XML instead of building large String payloads.",
            "Use byte arrays for binary data instead of Base64-encoded strings.",
        ],
        "code_example": """\
// BAD: O(n²) string allocations
String result = "";
for (String s : list) result += s + ",";

// GOOD: StringBuilder — O(n) with single allocation
StringBuilder sb = new StringBuilder();
for (String s : list) sb.append(s).append(',');""",
        "references": ["https://openjdk.org/jeps/192"],
    },

    "LARGE_CACHE": {
        "title": "Unbounded Cache / Collection",
        "root_cause": (
            "A cache or collection grows without a size limit or eviction policy, "
            "consuming an ever-increasing fraction of the heap."
        ),
        "fix_steps": [
            "Add a maximum size and eviction policy (LRU, LFU, TTL).",
            "Replace HashMap with Caffeine or Guava Cache.",
            "Use WeakHashMap so entries are evicted when key has no other referent.",
            "Expose cache size and hit/miss rate as metrics (Micrometer).",
        ],
        "code_example": """\
// BAD
private static final Map<String, Object> CACHE = new HashMap<>();

// GOOD (Caffeine)
private static final Cache<String, Object> CACHE =
    Caffeine.newBuilder()
        .maximumSize(10_000)
        .expireAfterWrite(10, TimeUnit.MINUTES)
        .recordStats()
        .build();""",
        "references": ["https://github.com/ben-manes/caffeine"],
    },

    "SYSTEM_CLASSLOADER_DOMINANCE": {
        "title": "System ClassLoader Dominates Heap",
        "root_cause": (
            "The system (bootstrap) ClassLoader retains a very large share of the heap. "
            "Usually means static data in JDK or third-party library classes is accumulating "
            "— string intern pools, logging buffers, static singleton caches."
        ),
        "fix_steps": [
            "In MAT, inspect the system ClassLoader's retained set.",
            "Look for static fields in library classes holding growing collections.",
            "Check logging configuration — oversized async appender queues.",
            "Call Introspector.flushCaches() on application shutdown.",
        ],
        "code_example": """\
Runtime.getRuntime().addShutdownHook(new Thread(() -> {
    Introspector.flushCaches();
    LogManager.shutdown();   // Log4j 2
}));""",
        "references": [],
    },

    "DUPLICATE_STRINGS": {
        "title": "Duplicate String Objects",
        "root_cause": (
            "Thousands of String objects with identical content exist on the heap. "
            "Common sources: JSON field names, enum-like string constants, "
            "database column names, or keys created via new String(literal)."
        ),
        "fix_steps": [
            "Enable JVM string deduplication: -XX:+UseStringDeduplication.",
            "Use enums or interned constants instead of raw String literals.",
            "Use String.intern() for high-frequency, low-cardinality strings.",
            "Avoid new String(\"literal\") — it bypasses the string pool.",
        ],
        "code_example": """\
// BAD: 100k identical String objects
for (int i = 0; i < 100_000; i++)
    list.add(new String("com.example.UserService"));

// GOOD: share the same reference
private static final String SERVICE = "com.example.UserService";
for (int i = 0; i < 100_000; i++)
    list.add(SERVICE);""",
        "references": [],
    },

    "EMPTY_COLLECTIONS": {
        "title": "Excessive Empty Collections",
        "root_cause": (
            "Many empty ArrayList / HashMap / HashSet instances occupy heap unnecessarily. "
            "Even an empty ArrayList allocates a backing Object[] array."
        ),
        "fix_steps": [
            "Return Collections.emptyList() / emptyMap() / emptySet() — shared singletons.",
            "Use List.of() / Map.of() / Set.of() (Java 9+) for small immutable collections.",
            "Initialise collections lazily — only when the first element is added.",
        ],
        "code_example": """\
// BAD: always allocates new ArrayList even for empty result
public List<User> findByRole(String role) {
    List<User> result = new ArrayList<>();
    if (role == null) return result;
    ...
}

// GOOD: shared empty singleton
public List<User> findByRole(String role) {
    if (role == null) return Collections.emptyList();
    return queryUsers(role);
}""",
        "references": [],
    },

    "PRIMITIVE_ARRAYS_CONSTANT": {
        "title": "Redundant Constant Primitive Arrays",
        "root_cause": (
            "Many primitive arrays (byte[], int[], char[]) contain identical constant data. "
            "Each copy wastes heap without adding information."
        ),
        "fix_steps": [
            "Define the array once as a private static final field.",
            "Copy it when mutation is needed: Arrays.copyOf(CONSTANT, len).",
            "For byte buffers: use a shared ByteBuffer or a pool.",
        ],
        "code_example": """\
// BAD: new byte[16] on every call
public byte[] defaultHeader() { return new byte[16]; }

// GOOD: shared constant, defensive copy when needed
private static final byte[] EMPTY_HEADER = new byte[16];
public byte[] defaultHeader() {
    return Arrays.copyOf(EMPTY_HEADER, EMPTY_HEADER.length);
}""",
        "references": [],
    },

    "HIGH_RETAINED_RATIO": {
        "title": "Object with High Retained-to-Shallow Heap Ratio",
        "root_cause": (
            "A small (low shallow heap) object retains a disproportionately large object tree "
            "(high retained heap). Releasing this 'anchor' object would free large memory."
        ),
        "fix_steps": [
            "Use MAT 'Path to GC Roots' on the anchor object.",
            "Add explicit null-out or close() at the lifecycle boundary.",
            "Apply the AutoCloseable / Dispose pattern for resource-holding objects.",
        ],
        "code_example": """\
// BAD: tiny anchor retains 2 GB object graph
class SessionManager {          // shallow: 32 bytes
    Map<String, Session> cache  // retained: 2 GB
        = new HashMap<>();      // ...held by a static field, never cleared
}

// GOOD: bounded cache with automatic eviction
private final Cache<String, Session> cache =
    Caffeine.newBuilder()
        .maximumSize(5000)
        .expireAfterAccess(30, TimeUnit.MINUTES)
        .build();""",
        "references": [],
    },

    "ARRAY_FILL_RATIO": {
        "title": "Arrays with Low Fill Ratio",
        "root_cause": (
            "Large arrays (typically ArrayList backing arrays or HashMap internal tables) "
            "are significantly under-filled, wasting heap space."
        ),
        "fix_steps": [
            "Use ArrayList.trimToSize() after bulk population.",
            "Construct ArrayList with the correct initial capacity.",
            "For maps: use the right load factor and initial capacity.",
            "Switch to dynamic collections instead of pre-allocating large fixed arrays.",
        ],
        "code_example": """\
// BAD: over-allocated backing array
List<String> list = new ArrayList<>(1_000_000);
list.add("only one element");

// GOOD: right-size after population
List<String> list = new ArrayList<>();
populateList(list);
((ArrayList<String>) list).trimToSize();  // or: List.copyOf(list)""",
        "references": [],
    },
}


# ── Base class ───────────────────────────────────────────────────────────────

class MATBaseAnalyzer(ABC):
    """
    Abstract base for MAT report analyzers.

    Subclasses implement:
      parse_report()    — populate self.report_data from loaded HTML
      generate_report() — produce the human-readable string report

    The base class provides:
      - ZIP extraction + HTML loading (single read per file)
      - Parsing helpers: _clean_text, _parse_size_to_mb, _parse_number, _short_classname
      - Visual helpers: _bar, _section, _banner, _severity_icon (module-level)
      - build_summary() — Java diagnostics + recommendations block
      - save_report() / save_json()
    """

    def __init__(self, zip_path: str, output_dir: Optional[str] = None) -> None:
        self.zip_path = Path(zip_path)
        if not self.zip_path.exists():
            raise FileNotFoundError(f"Report archive not found: {zip_path}")

        self.output_dir = (
            Path(output_dir) if output_dir
            else self.zip_path.parent / f"{self.zip_path.stem}_analyzed"
        )
        self.html_files: Dict[str, Dict[str, Any]] = {}
        self.report_data: Dict[str, Any] = {}

    # ── Parsing helpers ───────────────────────────────────────────────────────

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        text = html.unescape(text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[»›]|&raquo;|&rsaquo;", "", text)
        text = re.sub(r"Skip\s+to\s+main\s+content", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\.{10,}", "…", text)       # MAT truncation dots
        return re.sub(r"\s+", " ", text).strip()

    def _extract_text(self, element, max_len: int = 300) -> str:
        if element is None:
            return ""
        raw = self._clean_text(element.get_text(separator=" ", strip=True))
        return raw[:max_len] + ("…" if len(raw) > max_len else "")

    def _parse_size_to_mb(self, size_str: str) -> float:
        """
        Convert a size string to MB.
        Handles: "100 MB", "1.5 GB", "512 KB", "1,048,576 bytes",
        and raw byte counts like "104,889,144" (no unit → assumed bytes).
        MAT HTML table cells contain raw byte counts without any unit suffix.
        """
        if not size_str:
            return 0.0
        s = re.sub(r"(\d),(\d)", r"\1\2", str(size_str).strip())
        # Explicit unit
        m = re.search(r"([\d.]+)\s*(GB|MB|KB)\b", s, re.IGNORECASE)
        if m:
            val, unit = float(m.group(1)), m.group(2).upper()
            return val * 1024 if unit == "GB" else val / 1024 if unit == "KB" else val
        # "bytes" keyword
        bm = re.search(r"([\d]+)\s*bytes?", s, re.IGNORECASE)
        if bm:
            return int(bm.group(1)) / 1_048_576
        # Plain integer → assume bytes (MAT raw cell values)
        pm = re.fullmatch(r"\s*([\d]+)\s*", s)
        if pm:
            n = int(pm.group(1))
            return n / 1_048_576 if n >= 1024 else 0.0
        return 0.0

    def _parse_number(self, num_str: str) -> int:
        if not num_str:
            return 0
        try:
            return int(re.sub(r"[,.\s]", "", str(num_str).strip()))
        except ValueError:
            return 0

    def _short_classname(self, full: str, max_len: int = 55) -> str:
        """Shorten a fully-qualified class name, stripping MAT address suffixes."""
        if not full:
            return ""
        name = re.sub(r"\s*@\s*0x[0-9a-f]+", "", full).strip()
        name = re.sub(r"^(class|interface)\s+", "", name).strip()
        if len(name) <= max_len:
            return name
        parts = name.split(".")
        return ("…" + ".".join(parts[-2:])) if len(parts) > 2 else name[:max_len] + "…"

    # ── File I/O ──────────────────────────────────────────────────────────────

    def extract_report(self) -> Path:
        logger.info("Extracting %s → %s", self.zip_path, self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.zip_path, "r") as zf:
            zf.extractall(self.output_dir)
        return self.output_dir

    def load_html_files(self, pattern: str = "**/*.html") -> None:
        """Load every HTML file exactly once (fixes original double-read bug)."""
        files = list(self.output_dir.glob(pattern))
        logger.info("Loading %d HTML file(s)…", len(files))
        for html_file in files:
            try:
                content = html_file.read_text(encoding="utf-8", errors="replace")
                soup = BeautifulSoup(content, "html.parser")
                entry = {"content": content, "soup": soup, "path": html_file}
                rel = str(html_file.relative_to(self.output_dir))
                self.html_files[rel] = entry
                self.html_files[html_file.name] = entry    # short-name alias
            except Exception as exc:
                logger.warning("Could not load %s: %s", html_file, exc)

    def save_report(self, output_path: Optional[str] = None) -> Path:
        dest = Path(output_path) if output_path else self.output_dir / "analysis.txt"
        dest.write_text(self.generate_report(), encoding="utf-8")
        logger.info("Text report → %s", dest)
        return dest

    def save_json(self, output_path: Optional[str] = None) -> Path:
        dest = Path(output_path) if output_path else self.output_dir / "analysis.json"
        dest.write_text(json.dumps(self.report_data, indent=2, default=str), encoding="utf-8")
        logger.info("JSON → %s", dest)
        return dest

    # ── Java diagnostics engine ───────────────────────────────────────────────

    def build_summary(self) -> str:
        """
        Java diagnostics block appended to every report.
        One recommendation entry per unique problem type.
        """
        problems: List[Dict] = self.report_data.get("problems", [])
        warnings: List[Dict] = self.report_data.get("warnings", [])
        W = _W

        lines: List[str] = ["", _section("JAVA DIAGNOSTICS & RECOMMENDATIONS"), ""]

        if not problems and not warnings:
            lines += [
                "  ✅  No significant Java memory issues detected.",
                "",
                "  Baseline recommendations:",
                "    • Set -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/dumps/ on all JVMs.",
                "    • Capture heap snapshots periodically and compare for growth trends.",
                "    • Monitor GC pause times and allocation rates with JFR or async-profiler.",
                "",
                "═" * W,
            ]
            return "\n".join(lines)

        # Issue overview
        all_items = (
            [(p.get("severity", "HIGH"), p.get("type", "UNKNOWN"), p.get("description", ""))
             for p in problems]
            + [("WARN", w.get("type", "UNKNOWN"), w.get("description", ""))
               for w in warnings]
        )

        lines.append("  Issue summary:")
        lines.append("  " + "─" * (W - 4))
        for severity, ptype, desc in all_items:
            icon = _severity_icon(severity)
            short = desc[:W - 16] + "…" if len(desc) > W - 16 else desc
            lines.append(f"  {icon}  [{severity:<8}]  {short}")
        lines += ["", "  Recommendations:", "  " + "─" * (W - 4), ""]

        # Per-type recommendation blocks
        seen: set = set()
        num = 0
        for severity, ptype, _ in all_items:
            if ptype in seen:
                continue
            seen.add(ptype)

            rec = JAVA_RECOMMENDATIONS.get(ptype)
            if rec is None:
                for key, val in JAVA_RECOMMENDATIONS.items():
                    if key in ptype or ptype in key:
                        rec = val
                        break
            if rec is None:
                continue

            num += 1
            icon = _severity_icon(severity)
            lines.append(f"  {num}. {icon}  {rec['title']}")
            lines.append("")

            # Root cause — word-wrapped
            rc_words = rec["root_cause"].split()
            buf: List[str] = []
            first_line = True
            for w in rc_words:
                buf.append(w)
                if len(" ".join(buf)) > 65:
                    prefix = "     Root Cause:  " if first_line else "                  "
                    first_line = False
                    lines.append(f"{prefix}{' '.join(buf[:-1])}")
                    buf = [w]
            if buf:
                prefix = "     Root Cause:  " if first_line else "                  "
                lines.append(f"{prefix}{' '.join(buf)}")
            lines.append("")

            lines.append("     Fix Steps:")
            for i, step in enumerate(rec["fix_steps"], 1):
                lines.append(f"       {i}. {step}")
            lines.append("")

            if rec.get("references"):
                lines.append("     Further Reading:")
                for ref in rec["references"]:
                    lines.append(f"       • {ref}")
                lines.append("")

        lines += [
            _section("GENERAL BEST PRACTICES"),
            "",
            "  • Always analyse heap dumps captured under realistic production load.",
            "  • Compare snapshots over time to find monotonically growing object sets.",
            "  • Set -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/dumps/ on all JVMs.",
            "  • Integrate heap-dump analysis into your incident-response runbook.",
            "",
            "═" * W,
        ]
        return "\n".join(lines)

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def parse_report(self) -> None:
        """Parse loaded HTML files and populate self.report_data."""

    @abstractmethod
    def generate_report(self) -> str:
        """Return a human-readable analysis report (including build_summary())."""

    def analyze(self) -> "MATBaseAnalyzer":
        """Full pipeline: extract → load → parse. Returns self for chaining."""
        self.extract_report()
        self.load_html_files()
        self.parse_report()
        return self
