import java.io.*;
import java.lang.management.ManagementFactory;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import javax.management.MBeanServer;
import com.sun.management.HotSpotDiagnosticMXBean;

/**
 * JavaMemoryIssuesDemo
 * ====================
 * Demonstrates the 7 most common Java memory problems that appear clearly
 * in Eclipse Memory Analyzer Tool (MAT) reports. Each scenario uses named
 * inner classes so MAT reports show meaningful class names rather than
 * just raw byte[] entries.
 *
 *  1. Static Collection Leak      — unbounded static List, never cleared
 *  2. Cache Without Eviction       — HashMap used as cache, no size limit
 *  3. Event-Listener Leak          — listeners registered, never removed
 *  4. ThreadLocal Leak             — thread-pool threads keep data forever
 *  5. String Duplication           — thousands of identical String objects
 *  6. ClassLoader / Resource Leak  — large object graphs held by loaders
 *  7. Large Object Allocation      — continuous array allocation → OOM
 *
 * Each scenario tags its leaked objects with a named wrapper class so Eclipse
 * MAT "Leak Suspects" and "Top Components" reports show human-readable names.
 *
 * Usage:
 *   javac JavaMemoryIssuesDemo.java
 *   java -Xms256m -Xmx512m -XX:+HeapDumpOnOutOfMemoryError \
 *        -XX:HeapDumpPath=./heapdumps/oom_dump.hprof \
 *        JavaMemoryIssuesDemo all
 */
public class JavaMemoryIssuesDemo {

    // -------------------------------------------------------------------------
    // Named wrapper types — visible as clear class names in MAT reports
    // -------------------------------------------------------------------------

    /** Wraps a payload so MAT shows "LeakedSession" instead of "byte[]" */
    static class LeakedSession {
        private static final AtomicInteger SEQ = new AtomicInteger();
        final int    id      = SEQ.incrementAndGet();
        final byte[] payload;
        LeakedSession(int sizeBytes) { this.payload = new byte[sizeBytes]; }
    }

    /** Wraps a cache entry so MAT shows "UnboundedCacheEntry" */
    static class UnboundedCacheEntry {
        final String key;
        final byte[] data;
        final long   createdAt = System.currentTimeMillis();
        UnboundedCacheEntry(String key, int sizeBytes) {
            this.key  = key;
            this.data = new byte[sizeBytes];
        }
    }

    /** Holds a payload that should be released when listener is unregistered */
    static class LeakedListener implements Runnable {
        private static final AtomicInteger SEQ = new AtomicInteger();
        final int    id  = SEQ.incrementAndGet();
        final byte[] ctx;                          // event context — never freed
        LeakedListener(int sizeBytes) { this.ctx = new byte[sizeBytes]; }
        @Override public void run() { /* process event */ }
    }

    /** Holds a per-thread request context that is never removed */
    static class ThreadRequestContext {
        final String requestId = UUID.randomUUID().toString();
        final byte[] payload;
        ThreadRequestContext(int sizeBytes) { this.payload = new byte[sizeBytes]; }
    }

    // -------------------------------------------------------------------------
    // Global leak containers (static = GC can never collect them)
    // -------------------------------------------------------------------------

    /** Scenario 1 — grows without bound */
    private static final List<LeakedSession>                   STATIC_SESSIONS  = new ArrayList<>();
    /** Scenario 2 — no eviction policy */
    private static final Map<String, UnboundedCacheEntry>      APP_CACHE        = new HashMap<>();
    /** Scenario 3 — listeners never removed */
    private static final List<LeakedListener>                  EVENT_BUS        = new ArrayList<>();
    /** Scenario 4 — per-thread data never removed */
    private static final ThreadLocal<ThreadRequestContext>     REQUEST_CTX      = new ThreadLocal<>();

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------

    private static void generateHeapDump(String path) {
        try {
            Files.createDirectories(Paths.get(path).getParent());
            MBeanServer srv  = ManagementFactory.getPlatformMBeanServer();
            HotSpotDiagnosticMXBean bean = ManagementFactory.newPlatformMXBeanProxy(
                srv, "com.sun.management:type=HotSpotDiagnostic",
                HotSpotDiagnosticMXBean.class);
            bean.dumpHeap(path, false);
            System.out.printf("  [DUMP] %s  (%s)%n", Paths.get(path).toAbsolutePath(),
                humanSize(new File(path).length()));
        } catch (Exception e) {
            System.err.println("  [DUMP FAILED] " + e.getMessage());
        }
    }

    private static String humanSize(long bytes) {
        if (bytes >= 1_073_741_824L) return String.format("%.1f GB", bytes / 1_073_741_824.0);
        if (bytes >= 1_048_576L)     return String.format("%.1f MB", bytes / 1_048_576.0);
        return String.format("%d KB", bytes / 1024);
    }

    private static long usedMB() {
        Runtime rt = Runtime.getRuntime();
        return (rt.totalMemory() - rt.freeMemory()) / 1_048_576L;
    }

    private static void memStat(String label) {
        System.out.printf("  %-45s  heap used: %d MB%n", label, usedMB());
    }

    // =========================================================================
    // Scenario 1 — Static Collection Leak
    // =========================================================================
    private static void scenario1_staticCollectionLeak() {
        System.out.println("\n╔══ SCENARIO 1 — Static Collection Leak ══╗");
        System.out.println("║  A static List<LeakedSession> grows without bound.       ║");
        System.out.println("║  MAT: Leak Suspects shows LeakedSession as root cause.   ║");
        System.out.println("╚══════════════════════════════════════════════════════════╝");

        for (int i = 0; i < 80; i++) {
            STATIC_SESSIONS.add(new LeakedSession(1024 * 1024));  // 1 MB each
            if (i % 20 == 19) memStat("After " + (i + 1) + " LeakedSession objects:");
        }
        System.out.printf("  → STATIC_SESSIONS holds %d sessions (%d MB) — GC cannot reclaim%n%n",
            STATIC_SESSIONS.size(), STATIC_SESSIONS.size());
    }

    // =========================================================================
    // Scenario 2 — Cache Without Eviction
    // =========================================================================
    private static void scenario2_cacheWithoutEviction() {
        System.out.println("\n╔══ SCENARIO 2 — Cache Without Eviction ══╗");
        System.out.println("║  HashMap used as a session cache. No max-size, no TTL.   ║");
        System.out.println("║  MAT: Top Components shows UnboundedCacheEntry cluster.  ║");
        System.out.println("╚══════════════════════════════════════════════════════════╝");

        for (int i = 0; i < 50_000; i++) {
            String key = "user-session:" + UUID.randomUUID();
            APP_CACHE.put(key, new UnboundedCacheEntry(key, 1024));  // 1 KB each ≈ 50 MB
            if (i % 10_000 == 9_999) memStat("After " + (i + 1) + " cache entries:");
        }
        System.out.printf("  → APP_CACHE has %,d entries with no eviction policy%n%n",
            APP_CACHE.size());
    }

    // =========================================================================
    // Scenario 3 — Event-Listener Leak
    // =========================================================================
    private static void scenario3_listenerLeak() {
        System.out.println("\n╔══ SCENARIO 3 — Event-Listener Leak ══╗");
        System.out.println("║  LeakedListeners added to event bus; unregister() never called. ║");
        System.out.println("║  MAT: LeakedListener appears as a top retained-heap entry.      ║");
        System.out.println("╚══════════════════════════════════════════════════════════════════╝");

        for (int i = 0; i < 500; i++) {
            EVENT_BUS.add(new LeakedListener(100 * 1024));   // 100 KB each ≈ 50 MB
            if (i % 100 == 99) memStat("After " + (i + 1) + " listeners:");
        }
        System.out.printf("  → EVENT_BUS retains %d listeners — none ever unregistered%n%n",
            EVENT_BUS.size());
    }

    // =========================================================================
    // Scenario 4 — ThreadLocal Leak
    // =========================================================================
    private static void scenario4_threadLocalLeak() throws Exception {
        System.out.println("\n╔══ SCENARIO 4 — ThreadLocal Leak ══╗");
        System.out.println("║  Thread-pool threads store ThreadRequestContext in REQUEST_CTX.  ║");
        System.out.println("║  REQUEST_CTX.remove() is never called — memory stays on threads. ║");
        System.out.println("║  MAT: Thread Overview shows each thread retaining large payload.  ║");
        System.out.println("╚══════════════════════════════════════════════════════════════════╝");

        ExecutorService pool = Executors.newFixedThreadPool(10);
        CountDownLatch latch = new CountDownLatch(20);

        for (int i = 0; i < 20; i++) {
            pool.submit(() -> {
                // BUG: remove() intentionally omitted — simulates request-scoped leak
                REQUEST_CTX.set(new ThreadRequestContext(5 * 1024 * 1024));  // 5 MB per thread
                System.out.println("  Thread " + Thread.currentThread().getName()
                    + " stored 5 MB in ThreadRequestContext (not removed)");
                latch.countDown();
            });
        }
        latch.await(15, TimeUnit.SECONDS);
        pool.shutdown();
        pool.awaitTermination(5, TimeUnit.SECONDS);
        memStat("After all tasks complete (ThreadLocals not removed):");
        System.out.println("  → Each pooled thread retains its 5 MB ThreadRequestContext%n");
    }

    // =========================================================================
    // Scenario 5 — String Duplication
    // =========================================================================
    private static void scenario5_stringDuplication() {
        System.out.println("\n╔══ SCENARIO 5 — String Duplication ══╗");
        System.out.println("║  200,000 identical String objects created instead of sharing one.  ║");
        System.out.println("║  MAT Top Components: Duplicate Strings section highlights waste.   ║");
        System.out.println("╚══════════════════════════════════════════════════════════════════╝");

        // Use a list reference so GC cannot collect the strings
        List<String> strings = new ArrayList<>(200_000);
        for (int i = 0; i < 200_000; i++) {
            // new String(...) bypasses the string pool — each call creates a distinct object
            strings.add(new String("com.example.service.UserServiceImpl"));
            if (i % 50_000 == 49_999) memStat("After " + (i + 1) + " duplicate strings:");
        }
        // Anchor to static leak so GC cannot collect
        STATIC_SESSIONS.add(new LeakedSession(0));
        System.out.printf("  → %,d distinct String objects with identical content%n%n",
            strings.size());
    }

    // =========================================================================
    // Scenario 6 — ClassLoader / Large Object Graph Leak
    // =========================================================================

    /** Each instance holds a large payload — simulates a connection/resource object */
    static class ResourceHolder {
        private static final AtomicInteger SEQ = new AtomicInteger();
        final int    id      = SEQ.incrementAndGet();
        final byte[] buffer;                       // simulates I/O buffer
        final String name;

        ResourceHolder(String name, int bufSize) {
            this.name   = name;
            this.buffer = new byte[bufSize];
        }

        /** Non-static inner class — holds implicit reference to ResourceHolder */
        class UnclosedHandle {
            final long openedAt = System.currentTimeMillis();
            // close() intentionally never called → ResourceHolder.buffer stays alive
            public String toString() { return "UnclosedHandle#" + id + "@" + name; }
        }
    }

    private static final List<ResourceHolder.UnclosedHandle> OPEN_HANDLES = new ArrayList<>();

    private static void scenario6_resourceLeak() {
        System.out.println("\n╔══ SCENARIO 6 — Resource / Inner-Class Leak ══╗");
        System.out.println("║  Non-static inner class UnclosedHandle keeps ResourceHolder alive. ║");
        System.out.println("║  MAT: ResourceHolder.buffer[] shows as significant retained heap.  ║");
        System.out.println("╚══════════════════════════════════════════════════════════════════╝");

        for (int i = 0; i < 200; i++) {
            ResourceHolder holder = new ResourceHolder("db-conn-" + i, 5 * 1024 * 1024);
            OPEN_HANDLES.add(holder.new UnclosedHandle());  // inner class holds outer ref
            if (i % 50 == 49) memStat("After " + (i + 1) + " unclosed handles:");
        }
        System.out.printf("  → %d handles open; each keeps a 5 MB ResourceHolder alive%n%n",
            OPEN_HANDLES.size());
    }

    // =========================================================================
    // Scenario 7 — Large Object Allocation → OOM
    // =========================================================================
    private static void scenario7_largeObjectAllocation() {
        System.out.println("\n╔══ SCENARIO 7 — Large Object Allocation → OOM ══╗");
        System.out.println("║  Continuously allocate large byte arrays until OOM.               ║");
        System.out.println("║  -XX:+HeapDumpOnOutOfMemoryError writes oom_dump.hprof.            ║");
        System.out.println("╚══════════════════════════════════════════════════════════════════╝");

        List<byte[]> arrays = new ArrayList<>();
        try {
            for (int i = 0; ; i++) {
                arrays.add(new byte[20 * 1024 * 1024]);   // 20 MB each
                memStat("Allocated chunk #" + (i + 1) + " (20 MB):");
            }
        } catch (OutOfMemoryError oom) {
            System.out.printf("%n  [OOM] OutOfMemoryError after %d×20 MB = %d MB%n",
                arrays.size(), arrays.size() * 20);
            System.out.println("  → If -XX:+HeapDumpOnOutOfMemoryError was set, check heapdumps/");
        }
    }

    // =========================================================================
    // Main
    // =========================================================================
    public static void main(String[] args) throws Exception {
        System.out.println("╔══════════════════════════════════════════════════════╗");
        System.out.printf( "║  Java Memory Issues Demo   (heap: %d MB used / %d MB max)%n",
            usedMB(), Runtime.getRuntime().maxMemory() / 1_048_576L);
        System.out.println("╚══════════════════════════════════════════════════════╝");

        String mode       = args.length > 0 ? args[0].toLowerCase() : "menu";
        String dumpDir    = args.length > 1 ? args[1] : "./heapdumps";
        // Optional explicit dump file path — used by run-demo.sh per-scenario runs
        String customDump = args.length > 2 ? args[2] : null;

        // Shutdown hook only for interactive / all modes; single-scenario runs
        // dump explicitly so the hook would only produce a redundant extra file.
        if (!mode.matches("\\d+")) {
            Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                System.out.println("\n[SHUTDOWN] Generating heap dump…");
                generateHeapDump(dumpDir + "/demo_shutdown.hprof");
            }, "heap-dump-hook"));
        }

        switch (mode) {
            case "all":  runAll(dumpDir);                   break;
            case "menu": runMenu(dumpDir);                  break;
            default:
                if (mode.matches("\\d+")) {
                    int n = Integer.parseInt(mode);
                    runSingle(n, dumpDir, customDump);
                } else {
                    System.err.println("Usage: JavaMemoryIssuesDemo [all|menu|1-7] [dumpDir] [dumpFile]");
                    System.exit(1);
                }
        }
    }

    private static void runAll(String dumpDir) throws Exception {
        System.out.println("\nRunning ALL scenarios…\n");
        scenario1_staticCollectionLeak();
        scenario2_cacheWithoutEviction();
        scenario3_listenerLeak();
        scenario4_threadLocalLeak();
        scenario5_stringDuplication();
        scenario6_resourceLeak();
        // Dump heap BEFORE OOM scenario — captures leak state
        System.out.println("\n[HEAP DUMP] Capturing heap state before OOM scenario…");
        generateHeapDump(dumpDir + "/demo_before_oom.hprof");
        scenario7_largeObjectAllocation();
    }

    private static void runSingle(int n, String dumpDir, String customDump) throws Exception {
        switch (n) {
            case 1: scenario1_staticCollectionLeak();  break;
            case 2: scenario2_cacheWithoutEviction();  break;
            case 3: scenario3_listenerLeak();           break;
            case 4: scenario4_threadLocalLeak();        break;
            case 5: scenario5_stringDuplication();      break;
            case 6: scenario6_resourceLeak();           break;
            case 7: scenario7_largeObjectAllocation();  break;
            default:
                System.err.println("Scenario must be 1–7");
                System.exit(1);
        }
        if (n != 7) {  // scenario 7 triggers OOM — dump is written by the JVM automatically
            // Prefer explicit path supplied by the caller (e.g. run-demo.sh), fall back
            // to the legacy per-scenario name so direct invocations still work.
            String path = (customDump != null && !customDump.isEmpty())
                ? customDump
                : dumpDir + "/demo_scenario" + n + ".hprof";
            System.out.println("\n[HEAP DUMP] Capturing heap state after scenario " + n + "…");
            generateHeapDump(path);
        }
    }

    private static void runMenu(String dumpDir) throws Exception {
        Scanner sc = new Scanner(System.in);
        while (true) {
            System.out.println("\n┌─ Select a scenario ──────────────────────────────────┐");
            System.out.println("│  1  Static Collection Leak   (LeakedSession)         │");
            System.out.println("│  2  Cache Without Eviction   (UnboundedCacheEntry)   │");
            System.out.println("│  3  Event-Listener Leak      (LeakedListener)        │");
            System.out.println("│  4  ThreadLocal Leak         (ThreadRequestContext)   │");
            System.out.println("│  5  String Duplication                                │");
            System.out.println("│  6  Resource / Inner-Class Leak (UnclosedHandle)     │");
            System.out.println("│  7  Large Object → OOM                               │");
            System.out.println("│  8  Run All  │  d  Dump Now  │  0  Exit              │");
            System.out.println("└──────────────────────────────────────────────────────┘");
            System.out.print("Choice: ");
            String in = sc.nextLine().trim();
            switch (in) {
                case "1": scenario1_staticCollectionLeak();  break;
                case "2": scenario2_cacheWithoutEviction();  break;
                case "3": scenario3_listenerLeak();           break;
                case "4": scenario4_threadLocalLeak();        break;
                case "5": scenario5_stringDuplication();      break;
                case "6": scenario6_resourceLeak();           break;
                case "7": scenario7_largeObjectAllocation();  break;
                case "8": runAll(dumpDir);                    break;
                case "d": generateHeapDump(dumpDir + "/manual_" + System.currentTimeMillis() + ".hprof"); break;
                case "0": System.out.println("Bye."); return;
                default:  System.out.println("Unknown: " + in);
            }
        }
    }
}
