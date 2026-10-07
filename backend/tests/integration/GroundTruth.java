import java.lang.management.ManagementFactory;
import java.util.*;
import com.sun.management.HotSpotDiagnosticMXBean;

/** Known heap content for checking the analyzers against real Eclipse MAT output. */
public class GroundTruth {
  static final Map<String, byte[]> PRODUCT_CACHE = new HashMap<>();      // 120 x 1 MB, never evicted
  static final List<String> STATUS_TEXTS = new ArrayList<>();            // 200,000 equal strings
  static final List<Map<String, String>> EMPTY_MAPS = new ArrayList<>(); // 100,000 empty HashMaps
  static final ThreadLocal<byte[]> REQUEST_BUFFER = new ThreadLocal<>();
  static final List<String> COPIED_TEXTS = new ArrayList<>();             // 300,000 strings, each with its own byte[]
  static final List<List<String>> EMPTY_LISTS = new ArrayList<>();        // 200,000 empty ArrayLists with capacity 64

  public static void main(String[] a) throws Exception {
    for (int i = 0; i < 120; i++) PRODUCT_CACHE.put("product-" + i, new byte[1024 * 1024]);
    for (int i = 0; i < 200_000; i++) STATUS_TEXTS.add(new String("customer-status-ACTIVE"));
    for (int i = 0; i < 100_000; i++) EMPTY_MAPS.add(new HashMap<>());
    char[] text = "customer-status-ACTIVE".toCharArray();
    for (int i = 0; i < 300_000; i++) COPIED_TEXTS.add(new String(text));
    for (int i = 0; i < 200_000; i++) EMPTY_LISTS.add(new ArrayList<>(64));
    Thread t = new Thread(() -> {
      REQUEST_BUFFER.set(new byte[60 * 1024 * 1024]);                     // 60 MB, kept by a live thread
      try { Thread.sleep(Long.MAX_VALUE); } catch (InterruptedException e) { }
    }, "worker-tl");
    t.setDaemon(true); t.start();
    Thread.sleep(500);
    ManagementFactory.getPlatformMXBean(HotSpotDiagnosticMXBean.class).dumpHeap(a[0], true);   // live objects only
  }
}
