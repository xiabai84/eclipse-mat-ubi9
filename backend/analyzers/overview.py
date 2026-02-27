#!/usr/bin/env python3
"""
MAT System Overview Report Analyzer — redesigned edition.

Key fixes over previous version:
- CRITICAL: Thread name is in cols[1], NOT cols[0].
  cols[0] contains the thread object address link (e.g., java.lang.Thread @ 0x...).
  cols[1] contains the human-readable name (e.g., "main", "Thread-0").
- _parse_index() uses stored BeautifulSoup object for robust table parsing.
- Retained heap values in MAT tables are raw byte counts (no unit suffix).
  _parse_size_to_mb() in the base class handles this transparently.
- Thread, histogram and top-consumer parsers fall back to any matching file
  instead of relying on exact filename substrings.
- generate_report() completely redesigned with box-drawing characters,
  progress bars, severity icons and per-issue recommendations from base.build_summary().
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import MATBaseAnalyzer, _bar, _section, _banner, _severity_icon

logger = logging.getLogger(__name__)

_W = 80


class MATSystemOverviewAnalyzer(MATBaseAnalyzer):
    """Analyses Eclipse MAT System Overview ZIP reports."""

    def __init__(self, zip_path: str, output_dir: Optional[str] = None) -> None:
        super().__init__(zip_path, output_dir)
        self.report_data: Dict[str, Any] = {
            "summary": {
                "used_heap_raw": "",
                "used_heap_mb": 0.0,
                "total_objects": 0,
                "total_classes": 0,
                "total_classloaders": 0,
                "total_gc_roots": 0,
            },
            "thread_analysis": {
                "total_threads": 0,
                "threads": [],
                "potential_leaks": [],
            },
            "class_histogram": [],
            "top_consumers": [],
            "problems": [],
            "warnings": [],
        }

    # ── Parsing ───────────────────────────────────────────────────────────────

    def parse_report(self) -> None:
        if "index.html" in self.html_files:
            self._parse_index()

        # Thread overview: first file whose name contains "thread" (case-insensitive)
        for name in self.html_files:
            if re.search(r"thread", name, re.IGNORECASE) and "index" not in name.lower():
                self._parse_threads(name)
                break

        # Class histogram
        for name in self.html_files:
            if "histogram" in name.lower():
                self._parse_histogram(name)
                break

        # Top consumers
        for name in self.html_files:
            if "top_consumer" in name.lower() or "topconsumer" in name.lower():
                self._parse_top_consumers(name)
                break

        self._analyze_problems()

    # ── index.html ────────────────────────────────────────────────────────────

    def _parse_index(self) -> None:
        """Use BeautifulSoup to extract key metrics from index.html."""
        soup = self.html_files["index.html"]["soup"]

        # MAT renders the heap summary as an HTML table with label → value rows.
        label_map = {
            r"used heap":             ("used_heap_raw",        "string"),
            r"number of objects":     ("total_objects",        "int"),
            r"number of classes":     ("total_classes",        "int"),
            r"number of class loaders": ("total_classloaders", "int"),
            r"number of gc roots":    ("total_gc_roots",       "int"),
        }

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cols = row.find_all(["td", "th"])
                if len(cols) < 2:
                    continue
                label = cols[0].get_text(strip=True).lower()
                value = cols[1].get_text(strip=True)

                for pattern, (key, kind) in label_map.items():
                    if re.search(pattern, label):
                        if kind == "string":
                            self.report_data["summary"][key] = value
                            mb_key = key.replace("_raw", "_mb")
                            self.report_data["summary"][mb_key] = (
                                self._parse_size_to_mb(value)
                            )
                        else:
                            self.report_data["summary"][key] = self._parse_number(value)
                        break

    # ── Thread overview ───────────────────────────────────────────────────────

    def _parse_threads(self, filename: str) -> None:
        """Parse the thread overview table.

        MAT thread table column layout (0-indexed):
          0  Object / Stack Frame  — thread object address (e.g. java.lang.Thread @ 0x...)
          1  Name                  — human-readable name ("main", "Thread-0", …)
          2  Shallow Heap          — raw bytes (integer, no unit)
          3  Retained Heap         — raw bytes (integer, no unit)
          4  Max. Locals' Retained Heap
          5  Context Class Loader
          6  Is Daemon
          7  Priority
          8  State
          9  State value
        """
        soup = self.html_files[filename]["soup"]

        table = soup.find("table", class_="result") or soup.find("table")
        if not table:
            return

        rows = table.find_all("tr")
        if len(rows) < 2:
            return

        threads: List[Dict] = []
        for row in rows[1:]:
            cols = row.find_all(["td", "th"])
            if len(cols) < 2:
                continue

            # col[0] = object address link; col[1] = human-readable name
            name = self._clean_text(cols[1].get_text(strip=True)) if len(cols) > 1 else ""

            # Skip header or totals rows that sneak through
            if not name or name.lower() in ("name", "total", "totals"):
                continue

            shallow_raw = cols[2].get_text(strip=True) if len(cols) > 2 else ""
            retained_raw = cols[3].get_text(strip=True) if len(cols) > 3 else ""
            retained_mb = self._parse_size_to_mb(retained_raw)
            classloader = (
                self._clean_text(cols[5].get_text()) if len(cols) > 5 else ""
            )
            state = (
                self._clean_text(cols[8].get_text()) if len(cols) > 8 else ""
            )

            thread_info: Dict[str, Any] = {
                "name": name[:100],
                "shallow_raw": shallow_raw,
                "retained_raw": retained_raw,
                "retained_mb": retained_mb,
                "classloader": classloader,
                "state": state,
            }
            threads.append(thread_info)

            if retained_mb > 50:
                self.report_data["thread_analysis"]["potential_leaks"].append(
                    {
                        "thread": name[:100],
                        "retained_mb": retained_mb,
                        "concern": (
                            f"Thread '{name}' retains {retained_mb:.1f} MB — "
                            "possible thread-local leak"
                        ),
                    }
                )

        self.report_data["thread_analysis"]["threads"] = threads
        self.report_data["thread_analysis"]["total_threads"] = len(threads)

    # ── Class histogram ───────────────────────────────────────────────────────

    def _parse_histogram(self, filename: str) -> None:
        """Parse the class histogram (top-N classes by retained heap)."""
        soup = self.html_files[filename]["soup"]

        table = soup.find("table", class_="result") or soup.find("table")
        if not table:
            return

        rows = table.find_all("tr")
        histogram: List[Dict] = []
        for row in rows[1:21]:   # top 20
            cols = row.find_all(["td", "th"])
            if len(cols) < 4:
                continue
            class_name = self._clean_text(cols[0].get_text())
            obj_count = self._parse_number(cols[1].get_text())
            shallow = cols[2].get_text(strip=True)
            retained = cols[3].get_text(strip=True)

            if not class_name:
                continue

            histogram.append(
                {
                    "class": self._short_classname(class_name)[:120],
                    "objects": obj_count,
                    "shallow": shallow,
                    "shallow_mb": self._parse_size_to_mb(shallow),
                    "retained": retained,
                    "retained_mb": self._parse_size_to_mb(retained),
                }
            )

        self.report_data["class_histogram"] = histogram

    # ── Top consumers ─────────────────────────────────────────────────────────

    def _parse_top_consumers(self, filename: str) -> None:
        """Parse the top memory consumers page.

        This page has multiple table types:
          - Biggest Objects (Class Name | Shallow Heap | Retained Heap)
          - Biggest Top-Level Dominator Classes (Label | # Objects | Used | Retained | %)
          - Biggest Packages (Package | Retained Heap | %)

        We harvest the first table that has a recognisable object/heap header.
        """
        soup = self.html_files[filename]["soup"]

        consumers: List[Dict] = []
        seen: set = set()

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_text = rows[0].get_text().lower()

            # Skip tables that are clearly not about individual objects
            if "package" in header_text and "class" not in header_text:
                continue
            if not any(kw in header_text for kw in ("object", "heap", "class", "label")):
                continue

            for row in rows[1:11]:
                cols = row.find_all(["td", "th"])
                if len(cols) < 2:
                    continue

                # Determine which column holds the name and size
                # Biggest Objects: col[0]=class, col[1]=shallow, col[2]=retained
                # Dominator classes: col[0]=label, col[1]=# objects, col[2]=used, col[3]=retained
                name = self._short_classname(
                    self._clean_text(cols[0].get_text(separator=" "))
                )
                # Prefer the last numeric column as the retained size
                size = ""
                for col in reversed(cols[1:]):
                    txt = col.get_text(strip=True).replace(",", "")
                    if re.fullmatch(r"[\d.]+", txt) or re.search(r"[\d.]+ *(MB|GB)", txt):
                        size = col.get_text(strip=True)
                        break

                if not name or name in seen or len(name) < 2:
                    continue
                # Skip totals rows
                if re.search(r"total|remainder", name, re.IGNORECASE):
                    continue

                seen.add(name)
                size_mb = self._parse_size_to_mb(size)
                consumers.append(
                    {
                        "name": name[:100],
                        "size": size,
                        "size_mb": size_mb,
                    }
                )

            if consumers:
                break   # stop after first useful table

        # Sort by size descending
        consumers.sort(key=lambda c: c["size_mb"], reverse=True)
        self.report_data["top_consumers"] = consumers

    # ── Problem detection ─────────────────────────────────────────────────────

    def _analyze_problems(self) -> None:
        problems: List[Dict] = []
        warnings: List[Dict] = []
        s = self.report_data["summary"]

        # Heap size
        heap_mb = s["used_heap_mb"]
        if heap_mb > 2048:
            problems.append(
                {
                    "severity": "HIGH",
                    "type": "LARGE_HEAP",
                    "description": f"Very large heap in use: {heap_mb:.0f} MB",
                    "recommendation": "Investigate retained objects; reduce heap or tune JVM flags",
                }
            )
        elif heap_mb > 1024:
            problems.append(
                {
                    "severity": "MEDIUM",
                    "type": "LARGE_HEAP",
                    "description": f"Large heap in use: {heap_mb:.0f} MB",
                    "recommendation": "Consider tuning heap or reducing memory usage",
                }
            )

        # Object count
        obj = s["total_objects"]
        if obj > 1_000_000:
            problems.append(
                {
                    "severity": "HIGH",
                    "type": "HIGH_OBJECT_COUNT",
                    "description": f"Very high object count: {obj:,}",
                    "recommendation": "Investigate object creation patterns; consider pooling",
                }
            )
        elif obj > 500_000:
            warnings.append(
                {
                    "type": "ELEVATED_OBJECT_COUNT",
                    "description": f"Elevated object count: {obj:,}",
                }
            )

        # Class loaders
        cl = s["total_classloaders"]
        if cl > 20:
            problems.append(
                {
                    "severity": "HIGH",
                    "type": "HIGH_CLASSLOADER_COUNT",
                    "description": f"High classloader count: {cl} — probable classloader leak",
                    "recommendation": (
                        "Check for classloader leaks (common in hot-redeploy scenarios)"
                    ),
                }
            )
        elif cl > 10:
            warnings.append(
                {
                    "type": "ELEVATED_CLASSLOADER_COUNT",
                    "description": f"Elevated classloader count: {cl}",
                }
            )

        # GC roots
        gc = s["total_gc_roots"]
        if gc > 5000:
            problems.append(
                {
                    "severity": "MEDIUM",
                    "type": "HIGH_GC_ROOT_COUNT",
                    "description": f"High GC root count: {gc:,} — may impact GC performance",
                    "recommendation": "Investigate static references and long-lived objects",
                }
            )

        # Thread leaks
        for leak in self.report_data["thread_analysis"]["potential_leaks"]:
            mb = leak["retained_mb"]
            problems.append(
                {
                    "severity": "HIGH" if mb > 100 else "MEDIUM",
                    "type": "THREAD_LEAK",
                    "description": leak["concern"],
                    "recommendation": (
                        "Check thread-local variables and thread pool cleanup"
                    ),
                }
            )

        # Large arrays
        for cls in self.report_data["class_histogram"]:
            if "[]" in cls["class"] and cls["retained_mb"] > 100:
                problems.append(
                    {
                        "severity": "MEDIUM",
                        "type": "LARGE_ARRAYS",
                        "description": (
                            f"Large {cls['class']} retains {cls['retained_mb']:.1f} MB"
                        ),
                        "recommendation": (
                            "Review array sizes; consider streaming for large data"
                        ),
                    }
                )

        # Large String usage
        for cls in self.report_data["class_histogram"]:
            if "String" in cls["class"] and cls["retained_mb"] > 100:
                warnings.append(
                    {
                        "type": "LARGE_STRING_USAGE",
                        "description": (
                            f"Large String usage: {cls['class']} retains "
                            f"{cls['retained_mb']:.1f} MB"
                        ),
                    }
                )

        # Cache-like top consumers
        cache_terms = {"cache", "map", "table", "buffer", "pool"}
        for consumer in self.report_data["top_consumers"]:
            if any(t in consumer["name"].lower() for t in cache_terms):
                if consumer["size_mb"] > 100:
                    problems.append(
                        {
                            "severity": "MEDIUM",
                            "type": "LARGE_CACHE",
                            "description": (
                                f"Large cache/collection: {consumer['name'][:80]} "
                                f"({consumer['size']})"
                            ),
                            "recommendation": (
                                "Implement cache size limits and eviction policies"
                            ),
                        }
                    )

        self.report_data["problems"] = problems
        self.report_data["warnings"] = warnings

    # ── Report generation ─────────────────────────────────────────────────────

    def generate_report(self) -> str:
        W = _W
        lines: List[str] = []

        s = self.report_data["summary"]
        ta = self.report_data["thread_analysis"]
        probs = self.report_data["problems"]
        warns = self.report_data["warnings"]

        # ── Banner ────────────────────────────────────────────────────────────
        lines.append(_banner("SYSTEM OVERVIEW MEMORY REPORT", "Eclipse MAT Analysis"))
        lines.append("")

        # ── JVM Snapshot ──────────────────────────────────────────────────────
        lines.append(_section("JVM MEMORY SNAPSHOT"))
        lines.append("")
        heap_mb = s["used_heap_mb"]
        lines.append(f"  Used Heap        :  {s['used_heap_raw']}"
                     + (f"  ({heap_mb:.1f} MB)" if heap_mb else ""))
        lines.append(f"  Total Objects    :  {s['total_objects']:,}")
        lines.append(f"  Total Classes    :  {s['total_classes']:,}")
        lines.append(f"  Class Loaders    :  {s['total_classloaders']}")
        lines.append(f"  GC Roots         :  {s['total_gc_roots']:,}")
        lines.append(f"  Live Threads     :  {ta['total_threads']}")
        lines.append("")

        # ── Problems ──────────────────────────────────────────────────────────
        if probs:
            lines.append(_section("⚠  PROBLEMS DETECTED"))
            lines.append("")
            for p in probs:
                icon = _severity_icon(p["severity"])
                lines.append(f"  {icon}  [{p['severity']}]  {p['description']}")
                if p.get("recommendation"):
                    lines.append(f"          → {p['recommendation']}")
            lines.append("")

        if warns:
            lines.append(_section("WARNINGS"))
            lines.append("")
            for w in warns:
                lines.append(f"  🟡  {w['description']}")
            lines.append("")

        # ── Thread analysis ───────────────────────────────────────────────────
        if ta["threads"]:
            lines.append(_section("THREAD ANALYSIS"))
            lines.append("")
            lines.append(f"  Total Threads  :  {ta['total_threads']}")
            lines.append("")

            if ta["potential_leaks"]:
                lines.append("  ⚠  Threads Retaining > 50 MB (potential thread-local leaks):")
                for leak in ta["potential_leaks"]:
                    lines.append(
                        f"    🔴  {leak['thread'][:60]}  →  {leak['retained_mb']:.1f} MB"
                    )
                lines.append("")

            top5 = sorted(ta["threads"], key=lambda t: t["retained_mb"], reverse=True)[:5]
            lines.append("  Top 5 Threads by Retained Memory:")
            lines.append(
                f"  {'Name':<40}  {'Shallow':>10}  {'Retained':>12}  {'ClassLoader'}"
            )
            lines.append("  " + "─" * (W - 4))
            for t in top5:
                cl_short = (t["classloader"] or "—")[-30:]
                shallow_mb = self._parse_size_to_mb(t.get("shallow_raw", ""))
                shallow_disp = (
                    f"{shallow_mb:.1f} MB" if shallow_mb >= 0.01
                    else (t.get("shallow_raw") or "—")
                )
                retained_disp = (
                    f"{t['retained_mb']:.1f} MB"
                    if t["retained_mb"] >= 0.01
                    else (t["retained_raw"] or "—")
                )
                lines.append(
                    f"  {t['name'][:40]:<40}  {shallow_disp:>10}  {retained_disp:>12}  {cl_short}"
                )
            lines.append("")

        # ── Class histogram ───────────────────────────────────────────────────
        if self.report_data["class_histogram"]:
            lines.append(_section("TOP CLASSES BY RETAINED MEMORY"))
            lines.append("")
            lines.append(
                f"  {'Class':<45}  {'Shallow':>10}  {'Retained':>12}  {'Objects':>10}"
            )
            lines.append("  " + "─" * (W - 4))
            for c in self.report_data["class_histogram"][:12]:
                shallow_disp = (
                    f"{c['shallow_mb']:.1f} MB"
                    if c.get("shallow_mb", 0) >= 0.01
                    else (c.get("shallow") or "—")
                )
                ret_disp = (
                    f"{c['retained_mb']:.1f} MB"
                    if c["retained_mb"] >= 0.01
                    else c["retained"]
                )
                lines.append(
                    f"  {c['class'][:45]:<45}  {shallow_disp:>10}  {ret_disp:>12}  {c['objects']:>10,}"
                )
            lines.append("")

        # ── Top consumers ─────────────────────────────────────────────────────
        if self.report_data["top_consumers"]:
            lines.append(_section("TOP MEMORY CONSUMERS"))
            lines.append("")
            lines.append(
                f"  {'Consumer':<55}  {'Size':>12}"
            )
            lines.append("  " + "─" * (W - 4))
            for c in self.report_data["top_consumers"][:10]:
                size_disp = (
                    f"{c['size_mb']:.1f} MB"
                    if c["size_mb"] >= 0.01
                    else c["size"]
                )
                lines.append(f"  {c['name'][:55]:<55}  {size_disp:>12}")
            lines.append("")

        # ── Key recommendations ───────────────────────────────────────────────
        lines.append(_section("KEY RECOMMENDATIONS"))
        lines.append("")

        if not probs and not warns:
            lines.append("  ✓  No significant memory issues detected.")
            lines.append("  •  Continue monitoring memory usage patterns.")
            lines.append("  •  Establish baseline measurements for future comparison.")
        else:
            types = {p["type"] for p in probs}
            step = 1
            if "HIGH_CLASSLOADER_COUNT" in types:
                lines.append(
                    f"  {step}. Monitor classloader count across redeployments — "
                    "check for PermGen/Metaspace leaks."
                )
                step += 1
            if "THREAD_LEAK" in types:
                lines.append(
                    f"  {step}. Audit thread-local variables; ensure thread pools "
                    "are properly shut down."
                )
                step += 1
            if "LARGE_CACHE" in types:
                lines.append(
                    f"  {step}. Add size limits and eviction policies to all caches/maps."
                )
                step += 1
            if "HIGH_GC_ROOT_COUNT" in types:
                lines.append(
                    f"  {step}. Review static references; avoid storing large "
                    "object graphs in static fields."
                )
                step += 1
            if "HIGH_OBJECT_COUNT" in types:
                lines.append(
                    f"  {step}. Profile object allocation hot-spots; consider "
                    "pooling or lazy-loading."
                )
                step += 1
            if "LARGE_HEAP" in types:
                lines.append(
                    f"  {step}. Tune -Xmx; analyse retained object trees with MAT's "
                    "Dominator Tree view."
                )
                step += 1

        lines.append("")

        # ── Full recommendation engine from base class ─────────────────────
        lines.append(self.build_summary())

        return "\n".join(lines)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Analyse MAT System Overview report")
    parser.add_argument("zip_file", help="Path to *_System_Overview.zip")
    parser.add_argument("-o", "--output", help="Output directory")
    parser.add_argument("--json", action="store_true", help="Also save JSON data")
    args = parser.parse_args()

    try:
        analyzer = MATSystemOverviewAnalyzer(args.zip_file, args.output)
        analyzer.analyze()
        print(analyzer.generate_report())
        analyzer.save_report()
        if args.json:
            analyzer.save_json()
        return 1 if analyzer.report_data["problems"] else 0
    except Exception as exc:
        logger.exception("Analysis failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
