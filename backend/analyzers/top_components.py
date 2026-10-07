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

# MAT's checks per component: <h4>title</h4><div>verdict</div>. key, type, kind:
#   waste  - memory that could be saved (duplicate content, empty collections, ...): counted as waste
#   ratio  - low fill ratios: the bytes MAT names are *retained* by those collections, not wasted; they often
#            overlap with "Empty Collections" (the backing arrays) or are a big object that merely holds data
#   info   - reference / finalizer / map statistics
_CHECKS: Dict[str, tuple] = {
    "Duplicate Strings": ("duplicate_strings", "DUPLICATE_STRINGS", "waste"),
    "Empty Collections": ("empty_collections", "EMPTY_COLLECTIONS", "waste"),
    "Zero-Length Arrays": ("zero_length_arrays", "ZERO_LENGTH_ARRAYS", "waste"),
    "Primitive Arrays with a Constant Value": ("constant_primitive_arrays", "CONSTANT_PRIMITIVE_ARRAYS", "waste"),
    "Collection Fill Ratios": ("collection_fill_ratios", "LOW_FILL_COLLECTIONS", "ratio"),
    "Array Fill Ratios": ("array_fill_ratios", "LOW_FILL_ARRAYS", "ratio"),
    "Finalizer Statistics": ("finalizer_queue", "FINALIZER_QUEUE", "info"),
    "Map Collision Ratios": ("map_collisions", "MAP_COLLISIONS", "info"),
    "Soft Reference Statistics": ("soft_references", "SOFT_REFERENCES", "info"),
    "Weak Reference Statistics": ("weak_references", "WEAK_REFERENCES", "info"),
}
# MAT's wording when a check found nothing
_NOTHING_FOUND = re.compile(r"^(No |Component does not|Heap dump contains no|A total of)", re.IGNORECASE)

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
    "zero_length_arrays": "Use shared empty array constants instead of allocating new zero-length arrays",
    "constant_primitive_arrays": "Avoid allocating large primitive arrays that only hold a constant value",
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
        """Follow MAT's structure: index → one page per component (class loader) → its checks and top consumers."""
        if "index.html" in self.html_files:
            self._parse_index()
        for comp_file in self._component_pages():
            self._parse_component(comp_file)
        cls = self.report_data["classloaders"]
        self.report_data["summary"]["components_analyzed"] = len(cls)
        # MAT's "Size" of a component is not its share of the heap (the system class loader's "Size" is often the
        # whole heap), so sizes must not be summed. The share in % is reliable: the component with the largest
        # share gives the total (Size / share), and every component's share follows from it.
        if not self.report_data["summary"]["total_heap_mb"] and cls:
            main = max(cls, key=lambda c: c["heap_pct"])
            if main["heap_pct"] > 0:
                total = main["size_mb"] * 100 / main["heap_pct"]
                self.report_data["summary"]["total_heap_mb"] = total
                self.report_data["summary"]["total_heap_raw"] = f"{total:.1f} MB"
        total = self.report_data["summary"]["total_heap_mb"]
        for c in cls:
            c["retained_mb"] = c["heap_pct"] / 100 * total if total else c["size_mb"]
        cls.sort(key=lambda c: c["retained_mb"], reverse=True)
        for c in self.report_data["top_consumers"]:
            c["heap_pct"] = round(c["size_mb"] / total * 100, 2) if total else 0.0
        self.report_data["top_consumers"].sort(key=lambda c: c["size_mb"], reverse=True)
        self._analyze_problems()

    def _page(self, href: str) -> Optional[Dict[str, Any]]:
        return self.html_files.get(Path(href).name)

    def _component_pages(self) -> List[str]:
        """The component pages linked from index.html ("<name> (97%)")."""
        index = self.html_files.get("index.html")
        if not index:
            return []
        pages = []
        for a in index["soup"].find_all("a", href=True):
            if re.search(r"\(\d+%\)\s*$", a.get_text(" ", strip=True)) and self._page(a["href"]):
                pages.append(Path(a["href"]).name)
        return list(dict.fromkeys(pages))

    def _parse_component(self, filename: str) -> None:
        soup = self.html_files[filename]["soup"]
        title = soup.find("h2")
        if not title:
            return
        m = re.match(r"(.*?)\s*\((\d+)%\)\s*$", title.get_text(" ", strip=True))
        name, pct = (m.group(1), float(m.group(2))) if m else (title.get_text(" ", strip=True), 0.0)
        facts = title.find_next("div").get_text(" ", strip=True) if title.find_next("div") else ""
        size = re.search(r"Size:\s*([\d.,]+\s*[KMGT]?B)", facts)
        classes = re.search(r"Classes:\s*([\d.,]+k?)", facts)
        objects = re.search(r"Objects:\s*([\d.,]+[kmM]?)", facts)
        size_mb = self._parse_size_to_mb(size.group(1)) if size else 0.0
        self.report_data["classloaders"].append({
            "name": name,
            "size_mb": size_mb,                          # MAT's "Size" - may overlap with other components
            "retained_mb": 0.0,                          # share of the heap, set in parse_report()
            "retained_raw": size.group(1) if size else "",
            "heap_pct": pct,
            "classes": self._parse_count(classes.group(1)) if classes else 0,
            "objects": self._parse_count(objects.group(1)) if objects else 0,
        })
        for a in soup.find_all("a", href=True):
            if a.get_text(strip=True) == "Top Consumers" and self._page(a["href"]):
                self._parse_top_consumers(Path(a["href"]).name)
                break
        for h4 in soup.find_all("h4"):
            check = h4.get_text(" ", strip=True)
            div = h4.find_next("div")
            if check in _CHECKS and div is not None:
                self._record_check(name, check, div.get_text(" ", strip=True))

    def _parse_count(self, raw: str) -> int:
        """MAT abbreviates counts on component pages: "538", "250.3k", "1.2m"."""
        m = re.fullmatch(r"([\d.,]+)\s*([kKmM]?)", raw.strip())
        if not m:
            return 0
        value = self._parse_decimal(m.group(1), integral=not m.group(2))
        return int(round(value * {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2).lower()]))

    def _parse_top_consumers(self, filename: str) -> None:
        """Biggest Objects of a component: named objects with their retained heap (no tree levels, no duplicates)."""
        soup = self.html_files[filename]["soup"]
        for heading in soup.find_all(["h3", "h4", "h5"]):
            if heading.get_text(strip=True) != "Biggest Objects":
                continue
            table = heading.find_next("table")
            if not table:
                return
            header = [c.get_text(" ", strip=True).lower() for c in table.find("tr").find_all(["th", "td"])]
            col = next((i for i, h in enumerate(header) if "retained" in h), len(header) - 1)
            seen = {c["name"] for c in self.report_data["top_consumers"]}
            for row in table.find_all("tr")[1:11]:
                tds = row.find_all("td")
                if len(tds) <= col:
                    continue
                link = tds[0].find("a")
                raw = (link.get_text(" ", strip=True) if link else tds[0].get_text(" ", strip=True))
                obj = self._clean_name(re.sub(r"\s*@\s*0x[0-9a-f]+.*$", "", raw))
                if not obj or obj.lower().startswith("total") or obj in seen:
                    continue
                seen.add(obj)
                size_raw = tds[col].get_text(strip=True)
                self.report_data["top_consumers"].append(
                    {"name": obj, "size_mb": self._parse_size_to_mb(size_raw), "size_raw": size_raw, "heap_pct": 0.0})
            return

    _BYTES = re.compile(r"(?:retain|Total size is)\s*(?:>=\s*)?([\d.,]+)\s*bytes", re.IGNORECASE)
    _COUNT = re.compile(r"([\d.,]+)\s+(?:instances|occurrences)", re.IGNORECASE)
    _TOP_ELEMENTS = re.compile(r"\s*Top elements include:.*$", re.IGNORECASE)

    def _record_check(self, component: str, check: str, verdict: str) -> None:
        """MAT's verdict of one check. Components can overlap, so per check the largest component value counts."""
        key, ptype, kind = _CHECKS[check]
        verdict = re.sub(r"\s*Details\s*»?\s*$", "", verdict).strip()
        # "Top elements include: 95 × <string content>" - the heap's content, not needed for the analysis
        verdict = self._TOP_ELEMENTS.sub("", verdict)
        entry = self.report_data["waste_analysis"].setdefault(key, {
            "label": check, "type": ptype, "kind": kind, "count": 0,
            "wasted_mb": 0.0, "wasted_raw": "", "retained_mb": 0.0, "details": [],
        })
        if _NOTHING_FOUND.match(verdict):
            return
        mb = sum(self._parse_size_to_mb(b + " bytes") for b in self._BYTES.findall(verdict))
        count = sum(int(self._parse_decimal(c, integral=True)) for c in self._COUNT.findall(verdict))
        entry["details"].append(f"{component}: {verdict}")
        entry["count"] = max(entry["count"], count)
        if kind == "waste":
            entry["wasted_mb"] = max(entry["wasted_mb"], mb)
            entry["wasted_raw"] = f"{entry['wasted_mb']:.1f} MB"
        elif kind == "ratio":
            entry["retained_mb"] = max(entry["retained_mb"], mb)

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

    # ── Problem detection ─────────────────────────────────────────────────────

    def _analyze_problems(self) -> None:
        from config import get_top_components_thresholds
        thresholds = get_top_components_thresholds()

        problems: List[Dict] = []
        warnings: List[Dict] = []
        total_mb = self.report_data["summary"]["total_heap_mb"]

        # Application classes live in the application class loader, so one loader holding most of the heap is
        # normal - worth a look only when it is large.
        for cl in self.report_data["classloaders"][:3]:
            if cl["retained_mb"] > thresholds.dominant_classloader_mb:
                warnings.append({
                    "type": "DOMINANT_CLASSLOADER",
                    "description": f"Class loader '{cl['name'][:60]}' retains {cl['retained_mb']:.1f} MB ({cl['heap_pct']:.0f}% of heap)",
                })

        # Single objects that retain a large part of the heap
        for consumer in self.report_data["top_consumers"][:5]:
            mb, pct = consumer["size_mb"], consumer["heap_pct"]
            if mb > thresholds.dominant_consumer_mb or (pct > thresholds.dominant_consumer_pct and mb > thresholds.large_consumer_mb):
                problems.append({
                    "severity": "HIGH",
                    "type": "DOMINANT_CONSUMER",
                    "description": f"'{consumer['name'][:60]}' retains {mb:.1f} MB ({pct:.1f}% of heap)",
                    "recommendation": "Examine its retention path in MAT Dominator Tree",
                })
            elif mb > thresholds.large_consumer_mb:
                warnings.append({"type": "LARGE_CONSUMER", "description": f"'{consumer['name'][:60]}' retains {mb:.1f} MB"})

        for key, w in self.report_data["waste_analysis"].items():
            if not w["details"]:
                continue
            verdict = w["details"][0]
            if w["kind"] == "waste" and w["wasted_mb"] > thresholds.waste_problem_mb:
                problems.append({
                    "severity": "MEDIUM",
                    "type": w["type"],
                    "description": f"{w['label']}: {w['wasted_mb']:.1f} MB ({verdict[:160]})",
                    "recommendation": _WASTE_RECOMMENDATIONS.get(key, "Review and reduce unnecessary object allocations"),
                })
            elif w["kind"] == "waste" and w["wasted_mb"] > thresholds.waste_warning_mb:
                warnings.append({"type": w["type"], "description": f"{w['label']}: {w['wasted_mb']:.1f} MB ({verdict[:160]})"})
            elif w["kind"] == "ratio" and w["retained_mb"] > thresholds.waste_warning_mb:
                warnings.append({
                    "type": w["type"],
                    "description": f"{w['label']}: collections with a low fill ratio retain {w['retained_mb']:.1f} MB ({verdict[:160]})",
                })
            elif w["kind"] == "info" and w["type"] in ("FINALIZER_QUEUE", "MAP_COLLISIONS"):
                warnings.append({"type": w["type"], "description": f"{w['label']}: {verdict[:200]}"})

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
            from config import get_top_components_thresholds
            thresholds = get_top_components_thresholds()
            lines.append(_section("MEMORY WASTE ANALYSIS"))
            lines.append("")
            for waste_key, waste in non_zero_waste.items():
                wasted = waste["wasted_mb"]
                icon = "🔴" if wasted > thresholds.waste_problem_mb else ("🟡" if wasted > thresholds.waste_warning_mb else "🔵")
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
