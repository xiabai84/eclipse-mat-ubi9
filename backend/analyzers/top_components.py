#!/usr/bin/env python3
"""
MAT Top Components Report Analyzer — redesigned edition.

Key fixes over previous versions:
- All hardcoded page filenames removed.  Pages discovered dynamically:
    1. Following href links inside index.html classloader sections.
    2. Scanning every HTML file for known keyword signatures.
- MAT top-consumers tables come in several layouts; parser handles all of them:
    • Biggest Objects          : Class Name | Shallow | Retained
    • Biggest Dominator Classes: Label | #Objects | Used Heap | Retained | %
    • Biggest Packages         : Package | Retained | %
    • Waste checks             : Description | #Objects | Wasted Heap
- Raw byte counts in table cells handled via base-class _parse_size_to_mb().
- generate_report() redesigned with box-drawing, progress bars, severity icons
  and per-issue recommendations from base.build_summary().
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import MATBaseAnalyzer, _bar, _section, _banner, _severity_icon

logger = logging.getLogger(__name__)

_W = 80

# Signatures used to locate "memory waste" analysis pages
_WASTE_SIGNATURES: Dict[str, Dict[str, str]] = {
    "duplicate_strings": {
        "keywords": ["duplicate strings", "duplicated strings", "string deduplication"],
        "label": "Duplicate Strings",
        "type": "DUPLICATE_STRINGS",
    },
    "empty_collections": {
        "keywords": ["empty collection", "zero-size collection", "empty collections"],
        "label": "Empty Collections",
        "type": "EMPTY_COLLECTIONS",
    },
    "sparse_arrays": {
        "keywords": ["sparse array", "sparse arrays", "sparse collection"],
        "label": "Sparse Arrays",
        "type": "SPARSE_ARRAYS",
    },
    "finalizer_queue": {
        "keywords": ["finalizer", "finalization queue", "finalizable"],
        "label": "Finalizer Queue",
        "type": "FINALIZER_QUEUE",
    },
}

# Recommendations keyed by waste_key (module-level so class methods can access it)
_WASTE_RECOMMENDATIONS: Dict[str, str] = {
    "duplicate_strings": (
        "Enable -XX:+UseStringDeduplication (G1 GC) or intern "
        "frequently repeated strings"
    ),
    "empty_collections": (
        "Replace empty ArrayList/HashMap with Collections.empty*() singletons "
        "or lazy-initialise"
    ),
    "sparse_arrays": (
        "Replace sparse arrays with HashMap or SparseArray (Android) to avoid "
        "null-slot overhead"
    ),
    "finalizer_queue": (
        "Avoid finalizers — use try-with-resources and java.lang.ref.Cleaner instead"
    ),
}


class MATTopComponentsAnalyzer(MATBaseAnalyzer):
    """Analyses Eclipse MAT Top Components ZIP reports."""

    def __init__(self, zip_path: str, output_dir: Optional[str] = None) -> None:
        super().__init__(zip_path, output_dir)
        self.report_data: Dict[str, Any] = {
            "summary": {
                "total_heap_mb": 0.0,
                "total_heap_raw": "",
                "components_analyzed": 0,
            },
            "classloaders": [],     # list of {name, retained_mb, retained_raw, objects}
            "top_consumers": [],    # list of {name, size_mb, size_raw}
            "waste_analysis": {},   # keyed by waste type
            "problems": [],
            "warnings": [],
        }

    # ── Parsing ───────────────────────────────────────────────────────────────

    def parse_report(self) -> None:
        if "index.html" in self.html_files:
            self._parse_index()

        # Scan all pages for classloader sections, consumers, and waste checks
        for filename, entry in self.html_files.items():
            content_lower = entry["content"].lower()

            if "classloader" in content_lower or "class loader" in content_lower:
                self._parse_classloaders(filename)

            if any(
                kw in content_lower
                for kw in ("biggest object", "top consumer", "retained heap", "dominator")
            ):
                self._parse_top_consumers_page(filename)

            for waste_key, sig in _WASTE_SIGNATURES.items():
                if any(kw in content_lower for kw in sig["keywords"]):
                    if waste_key not in self.report_data["waste_analysis"]:
                        self._parse_waste_section(filename, waste_key, sig)

        self._deduplicate_consumers()
        self._analyze_problems()

    # ── index.html ────────────────────────────────────────────────────────────

    def _parse_index(self) -> None:
        """Extract total heap summary from index.html."""
        content = self.html_files["index.html"]["content"]

        # Total heap: four strategies, most-specific first.
        # Strategy 1: alt= of a "Pie chart" <img> with "Total:" inside
        total_m = re.search(
            r'alt="Pie chart[^"]*Total:\s*([\d,.]+)\s*(MB|GB|B)?"',
            content,
            re.IGNORECASE,
        )
        # Strategy 2: any alt= attribute containing "Total:"
        if not total_m:
            total_m = re.search(
                r'alt="[^"]*Total:\s*([\d,.]+)\s*(MB|GB|B)?"',
                content,
                re.IGNORECASE,
            )
        # Strategy 3: "Total: X [unit?]" anywhere in the HTML
        #   Only accept raw-byte values ≥ 1 MB or values with an explicit unit.
        if not total_m:
            for m3 in re.finditer(
                r'\bTotal:\s*([\d,.]+)\s*(MB|GB|B)?(?=[^a-zA-Z\d]|$)',
                content,
                re.IGNORECASE,
            ):
                val3 = m3.group(1).replace(",", "")
                unit3 = (m3.group(2) or "").upper()
                try:
                    num3 = float(val3)
                except ValueError:
                    continue
                if unit3 in ("MB", "GB", "B") or num3 >= 1_048_576:
                    total_m = m3
                    break

        if total_m:
            val_str = total_m.group(1)
            unit = (total_m.group(2) or "").upper()
            parse_input = f"{val_str} {unit}".strip() if unit else val_str
            mb = self._parse_size_to_mb(parse_input)
            heap_label = f"{mb:.1f} MB" if unit not in ("MB", "GB") else f"{val_str} {unit}"
            self.report_data["summary"]["total_heap_raw"] = heap_label
            self.report_data["summary"]["total_heap_mb"] = mb

    # ── Classloader section ───────────────────────────────────────────────────

    def _parse_classloaders(self, filename: str) -> None:
        """Extract classloader → retained-size data from any matching page."""
        soup = self.html_files[filename]["soup"]
        existing_names = {c["name"] for c in self.report_data["classloaders"]}

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_text = rows[0].get_text().lower()
            if not any(kw in header_text for kw in ("class loader", "classloader", "loader")):
                continue
            if not any(kw in header_text for kw in ("heap", "retained", "object", "size")):
                continue

            for row in rows[1:]:
                cols = row.find_all(["td", "th"])
                if len(cols) < 2:
                    continue
                name = self._short_classname(
                    self._clean_text(cols[0].get_text(separator=" "))
                )
                if not name or name in existing_names or len(name) < 3:
                    continue
                if re.search(r"total|remainder", name, re.IGNORECASE):
                    continue

                sizes = []
                obj_count = 0
                heap_pct = 0.0
                for col in cols[1:]:
                    txt = col.get_text(strip=True)
                    mb = self._parse_size_to_mb(txt)
                    if mb > 0:
                        sizes.append((mb, txt))
                    else:
                        n = self._parse_number(txt)
                        if n > 0 and obj_count == 0:
                            obj_count = n
                    # Capture percentage column (handles European locale)
                    if not heap_pct:
                        pct_m = re.search(r'([\d]+[.,][\d]+|[\d]+)\s*%', txt)
                        if pct_m:
                            pct = float(pct_m.group(1).replace(',', '.'))
                            if 0 < pct < 100:
                                heap_pct = pct

                if not sizes:
                    continue

                retained_mb, retained_raw = max(sizes, key=lambda x: x[0])
                existing_names.add(name)
                self.report_data["classloaders"].append(
                    {
                        "name": name[:100],
                        "retained_mb": retained_mb,
                        "retained_raw": retained_raw,
                        "objects": obj_count,
                        "heap_pct": heap_pct,
                    }
                )

        self.report_data["classloaders"].sort(
            key=lambda c: c["retained_mb"], reverse=True
        )

    # ── Top consumers ─────────────────────────────────────────────────────────

    def _parse_top_consumers_page(self, filename: str) -> None:
        """Harvest big-object / dominator entries from any consumers page."""
        soup = self.html_files[filename]["soup"]
        existing = {c["name"] for c in self.report_data["top_consumers"]}

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header_text = rows[0].get_text().lower()
            if not any(kw in header_text for kw in ("object", "class", "heap", "label", "retained")):
                continue
            if "package" in header_text and "heap" not in header_text:
                continue

            for row in rows[1:11]:
                cols = row.find_all(["td", "th"])
                if len(cols) < 2:
                    continue

                raw_name = self._clean_text(cols[0].get_text(separator=" "))

                # Skip MAT tree-navigation rows entirely — MAT prefixes package/
                # class hierarchy nodes with "\ ", ".\ ", "..\ " etc.  These are
                # duplicates of the fully-qualified class entries that follow.
                if re.match(r'^\.{0,3}[/\\]', raw_name):
                    continue

                # Strip trailing "First N of M objects" expand-indicator text
                raw_name = re.sub(
                    r'\s+First\s+[\d,]+\s+of\s+[\d,]+\s+objects?.*',
                    '', raw_name, flags=re.IGNORECASE,
                ).strip()

                name = self._short_classname(raw_name)

                if not name or name in existing or len(name) < 2:
                    continue
                # Skip pure numbers, size values, and comparison-operator labels
                # e.g. "24", "20,971,520", "<= 1.00"
                if re.match(r'^[<>=\s\d,.\-]+$', name):
                    continue
                if re.search(r'^(total|remainder|first \d+)', name, re.IGNORECASE):
                    continue

                best_mb, best_raw = 0.0, ""
                heap_pct = 0.0
                for col in cols[1:]:
                    txt = col.get_text(strip=True)
                    mb = self._parse_size_to_mb(txt)
                    if mb > best_mb:
                        best_mb, best_raw = mb, txt
                    # Read heap-percentage column so we can derive total heap later.
                    # Handle both US ("29.37 %") and European ("29,37 %") decimal formats.
                    if not heap_pct:
                        pct_m = re.search(r'([\d]+[.,][\d]+|[\d]+)\s*%', txt)
                        if pct_m:
                            pct = float(pct_m.group(1).replace(',', '.'))
                            if 0 < pct < 100:
                                heap_pct = pct

                if best_mb <= 0:
                    continue

                existing.add(name)
                self.report_data["top_consumers"].append(
                    {
                        "name": name[:100],
                        "size_mb": best_mb,
                        "size_raw": best_raw,
                        "heap_pct": heap_pct,
                    }
                )

    # ── Waste analysis ────────────────────────────────────────────────────────

    def _parse_waste_section(
        self, filename: str, waste_key: str, sig: Dict[str, str]
    ) -> None:
        """Extract headline numbers from a memory-waste analysis section."""
        soup = self.html_files[filename]["soup"]
        content = self.html_files[filename]["content"]

        waste_record: Dict[str, Any] = {
            "label": sig["label"],
            "type": sig["type"],
            "count": 0,
            "wasted_mb": 0.0,
            "wasted_raw": "",
            "details": [],
        }

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            header = rows[0].get_text().lower()
            has_keyword = any(kw in header for kw in sig["keywords"])
            has_size = any(kw in header for kw in ("heap", "wasted", "size", "bytes"))
            if not (has_keyword or has_size):
                continue

            for row in rows[1:6]:
                cols = row.find_all(["td", "th"])
                if len(cols) < 2:
                    continue
                name = self._clean_text(cols[0].get_text())
                if not name:
                    continue
                for col in cols[1:]:
                    txt = col.get_text(strip=True)
                    mb = self._parse_size_to_mb(txt)
                    if mb > 0:
                        waste_record["wasted_mb"] += mb
                        if not waste_record["wasted_raw"]:
                            waste_record["wasted_raw"] = txt
                    n = self._parse_number(txt)
                    if n > 0 and waste_record["count"] == 0:
                        waste_record["count"] = n
                detail_str = f"{name}: {cols[1].get_text(strip=True)}"
                waste_record["details"].append(detail_str[:100])

            if waste_record["wasted_mb"] > 0 or waste_record["details"]:
                break

        # Fallback: regex on raw content
        if waste_record["wasted_mb"] == 0:
            for pat in (
                r"wasted\s+([\d,.]+)\s*(MB|GB)",
                r"([\d,.]+)\s*(MB|GB)\s+wasted",
                r"total\s+([\d,.]+)\s*(MB|GB)",
            ):
                m = re.search(pat, content, re.IGNORECASE)
                if m:
                    val = m.group(1).replace(",", "")
                    unit = m.group(2)
                    waste_record["wasted_mb"] = (
                        float(val) * 1024 if unit.upper() == "GB" else float(val)
                    )
                    waste_record["wasted_raw"] = f"{val} {unit}"
                    break

        self.report_data["waste_analysis"][waste_key] = waste_record

    # ── Post-processing ───────────────────────────────────────────────────────

    def _deduplicate_consumers(self) -> None:
        """Sort top_consumers by size, remove duplicates, derive total heap."""
        seen: set = set()
        unique = []
        for c in sorted(
            self.report_data["top_consumers"],
            key=lambda x: x["size_mb"],
            reverse=True,
        ):
            if c["name"] not in seen:
                seen.add(c["name"])
                unique.append(c)
        self.report_data["top_consumers"] = unique[:20]
        self.report_data["summary"]["components_analyzed"] = len(unique)

        # Derive total heap from the largest consumer whose heap_pct was read
        # from the table — this covers reports where the pie-chart img is absent.
        if self.report_data["summary"]["total_heap_mb"] == 0.0:
            for c in unique:
                pct = c.get("heap_pct", 0.0)
                mb = c.get("size_mb", 0.0)
                if pct > 0 and mb > 0:
                    derived = mb / (pct / 100.0)
                    self.report_data["summary"]["total_heap_mb"] = derived
                    self.report_data["summary"]["total_heap_raw"] = f"{derived:.1f} MB"
                    logger.debug(
                        "Total heap derived from consumer pct: %.1f MB (pct=%.2f%%)",
                        derived, pct,
                    )
                    break

        # Fallback: derive from classloaders table if consumers had no pct data.
        if self.report_data["summary"]["total_heap_mb"] == 0.0:
            for cl in self.report_data["classloaders"]:
                pct = cl.get("heap_pct", 0.0)
                mb = cl.get("retained_mb", 0.0)
                if pct > 0 and mb > 0:
                    derived = mb / (pct / 100.0)
                    self.report_data["summary"]["total_heap_mb"] = derived
                    self.report_data["summary"]["total_heap_raw"] = f"{derived:.1f} MB"
                    logger.debug(
                        "Total heap derived from classloader pct: %.1f MB (pct=%.2f%%)",
                        derived, pct,
                    )
                    break

    # ── Problem detection ─────────────────────────────────────────────────────

    def _analyze_problems(self) -> None:
        problems: List[Dict] = []
        warnings: List[Dict] = []

        total_mb = self.report_data["summary"]["total_heap_mb"]

        # Classloader dominance
        for cl in self.report_data["classloaders"][:3]:
            mb = cl["retained_mb"]
            if mb > 200:
                pct = (mb / total_mb * 100) if total_mb > 0 else 0
                problems.append(
                    {
                        "severity": "HIGH" if pct > 50 else "MEDIUM",
                        "type": "DOMINANT_CLASSLOADER",
                        "description": (
                            f"ClassLoader '{cl['name'][:60]}' retains "
                            f"{mb:.1f} MB ({pct:.1f}% of heap)"
                        ),
                        "recommendation": (
                            "Investigate classes loaded by this classloader; "
                            "check for classloader leak"
                        ),
                    }
                )

        # Large individual consumers
        for consumer in self.report_data["top_consumers"][:5]:
            mb = consumer["size_mb"]
            pct = (mb / total_mb * 100) if total_mb > 0 else 0
            if mb > 500 or pct > 40:
                problems.append(
                    {
                        "severity": "HIGH",
                        "type": "DOMINANT_CONSUMER",
                        "description": (
                            f"'{consumer['name'][:60]}' consumes "
                            f"{mb:.1f} MB ({pct:.1f}% of heap)"
                        ),
                        "recommendation": "Examine its retention path in MAT Dominator Tree",
                    }
                )
            elif mb > 100:
                warnings.append(
                    {
                        "type": "LARGE_CONSUMER",
                        "description": f"'{consumer['name'][:60]}' consumes {mb:.1f} MB",
                    }
                )

        # Waste analysis issues
        for waste_key, waste in self.report_data["waste_analysis"].items():
            wasted = waste["wasted_mb"]
            if wasted > 50:
                problems.append(
                    {
                        "severity": "MEDIUM",
                        "type": waste["type"],
                        "description": (
                            f"{waste['label']}: {wasted:.1f} MB wasted"
                            + (f" ({waste['count']:,} instances)" if waste["count"] else "")
                        ),
                        "recommendation": _WASTE_RECOMMENDATIONS.get(
                            waste_key,
                            "Review and reduce unnecessary object allocations",
                        ),
                    }
                )
            elif wasted > 10:
                warnings.append(
                    {
                        "type": waste["type"],
                        "description": f"{waste['label']}: {wasted:.1f} MB wasted",
                    }
                )

        self.report_data["problems"] = problems
        self.report_data["warnings"] = warnings

    # ── Report generation ──────────────────────────────────────────────────────

    def generate_report(self) -> str:
        W = _W
        lines: List[str] = []

        s = self.report_data["summary"]
        probs = self.report_data["problems"]
        warns = self.report_data["warnings"]
        total_mb = s["total_heap_mb"]

        # ── Banner ─────────────────────────────────────────────────────────────
        lines.append(_banner("TOP COMPONENTS MEMORY REPORT", "Eclipse MAT Analysis"))
        lines.append("")

        # ── Heap overview ──────────────────────────────────────────────────────
        lines.append(_section("HEAP OVERVIEW"))
        lines.append("")
        lines.append(f"  Total Heap           :  {s['total_heap_raw'] or 'n/a'}")
        lines.append(f"  Components Analysed  :  {s['components_analyzed']}")
        lines.append("")

        # ── Problems ───────────────────────────────────────────────────────────
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

        # ── Classloaders ───────────────────────────────────────────────────────
        if self.report_data["classloaders"]:
            lines.append(_section("CLASSLOADERS BY RETAINED MEMORY"))
            lines.append("")
            lines.append(
                f"  {'ClassLoader':<50}  {'Retained':>12}  {'Heap %':>7}  {'Objects':>10}"
            )
            lines.append("  " + "─" * (W - 4))
            for cl in self.report_data["classloaders"][:10]:
                pct = (cl["retained_mb"] / total_mb * 100) if total_mb > 0 else 0
                obj_str = f"{cl['objects']:,}" if cl["objects"] else "—"
                size_disp = (
                    f"{cl['retained_mb']:.1f} MB"
                    if cl["retained_mb"] >= 0.01
                    else cl["retained_raw"]
                )
                lines.append(
                    f"  {cl['name'][:50]:<50}  {size_disp:>12}  {pct:>6.1f}%  {obj_str:>10}"
                )
            lines.append("")

        # ── Top consumers ──────────────────────────────────────────────────────
        if self.report_data["top_consumers"]:
            lines.append(_section("TOP MEMORY CONSUMERS"))
            lines.append("")
            lines.append(
                f"  {'Consumer':<55}  {'Size':>12}  {'Heap %':>7}"
            )
            lines.append("  " + "─" * (W - 4))
            for c in self.report_data["top_consumers"][:12]:
                pct = (c["size_mb"] / total_mb * 100) if total_mb > 0 else 0
                size_disp = (
                    f"{c['size_mb']:.1f} MB"
                    if c["size_mb"] >= 0.01
                    else c["size_raw"]
                )
                lines.append(
                    f"  {c['name'][:55]:<55}  {size_disp:>12}  {pct:>6.1f}%"
                )
            lines.append("")

        # ── Waste analysis — only shown when at least one category > 0 MB ──────
        non_zero_waste = {
            k: v for k, v in self.report_data["waste_analysis"].items()
            if v["wasted_mb"] > 0
        }
        if non_zero_waste:
            lines.append(_section("MEMORY WASTE ANALYSIS"))
            lines.append("")
            for waste_key, waste in non_zero_waste.items():
                wasted = waste["wasted_mb"]
                icon = "🔴" if wasted > 50 else ("🟡" if wasted > 10 else "🔵")
                count_str = f"  ({waste['count']:,} instances)" if waste["count"] else ""
                lines.append(
                    f"  {icon}  {waste['label']}: {wasted:.1f} MB wasted{count_str}"
                )
                if waste.get("details"):
                    for detail in waste["details"][:3]:
                        lines.append(f"       • {detail}")
                rec = _WASTE_RECOMMENDATIONS.get(waste_key)
                if rec:
                    lines.append(f"       → Fix: {rec}")
                lines.append("")

        # ── Key recommendations — only rendered when there is actionable content ─
        types = {p["type"] for p in probs}
        rec_lines: List[str] = []
        step = 1

        if not probs and not warns:
            rec_lines.append("  ✓  No dominant memory consumers detected.")
            rec_lines.append("  •  Memory is well-distributed across components.")
        else:
            if "DOMINANT_CLASSLOADER" in types:
                rec_lines.append(
                    f"  {step}. Use MAT's Dominator Tree to trace the dominant "
                    "classloader's retention path."
                )
                step += 1
            if "DOMINANT_CONSUMER" in types:
                rec_lines.append(
                    f"  {step}. Drill into the largest consumer with MAT's OQL or "
                    "Path to GC Roots feature."
                )
                step += 1
            if "DUPLICATE_STRINGS" in types:
                rec_lines.append(
                    f"  {step}. Enable JVM string deduplication "
                    "(-XX:+UseStringDeduplication, G1 GC required)."
                )
                step += 1
            if "EMPTY_COLLECTIONS" in types:
                rec_lines.append(
                    f"  {step}. Replace empty ArrayList/HashMap instances with "
                    "Collections.emptyList() / emptyMap() singletons."
                )
                step += 1
            if "FINALIZER_QUEUE" in types:
                rec_lines.append(
                    f"  {step}. Remove finalizer() overrides; use try-with-resources "
                    "or java.lang.ref.Cleaner."
                )
                step += 1

        if rec_lines:
            lines.append(_section("KEY RECOMMENDATIONS"))
            lines.append("")
            lines += rec_lines
            lines.append("")

        # ── Full recommendation engine from base class ─────────────────────
        lines.append(self.build_summary())

        return "\n".join(lines)
