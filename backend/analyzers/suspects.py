#!/usr/bin/env python3
"""
MAT Leak Suspects Report Analyzer — redesigned edition.

Key fixes over previous version:
- CRITICAL: area_pat now matches alt= attribute (not title=) which is what
  Eclipse MAT actually emits for <area> tags in the pie chart.
- Suspect class name and classloader extracted directly from <q> tags in the
  "important" div, not from regexes on raw HTML entities.
- Percentage extracted from the bold "SIZE (PCT%)" pattern beside the class name.
- Thread column: MAT thread overview puts object address in col[0], name in col[1].
- Raw byte counts (no unit suffix) handled via base _parse_size_to_mb fallback.
- generate_report() completely redesigned with box-drawing, progress bars,
  severity icons and per-issue recommendations sourced from base.build_summary().
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import MATBaseAnalyzer, _bar, _section, _banner, _severity_icon

logger = logging.getLogger(__name__)

_W = 80

# MAT emits "occupy" (plural) or "occupies" (singular) depending on instance count.
# Numbers may use European locale: dot as thousands sep, comma as decimal sep.
# e.g.  "1.675 instances … occupy 564.224 (29,37 %) bytes"
#        → 564224 bytes, 29.37 %
_OCC_PAT = re.compile(
    r"occup(?:ies|y)\s+([\d.,]+)\s*\(([\d.,]+)\s*%\)",
    re.IGNORECASE,
)


def _parse_occ_bytes(raw: str) -> int:
    """Parse a MAT byte count that may use European thousands separators."""
    # Strip every separator character; what remains is the integer value.
    return int(re.sub(r"[.,]", "", raw))


def _parse_occ_pct(raw: str) -> float:
    """Parse a MAT percentage that may use a comma as the decimal separator."""
    return float(raw.replace(",", "."))


def _size_label(s: dict) -> str:
    """Return the best human-readable size string for a suspect dict.

    Falls back through retained_raw → retained_mb → heap_pct so that
    '0.0 MB' is never shown when a more meaningful value exists.
    """
    if s.get("retained_raw"):
        return s["retained_raw"]
    mb = s.get("retained_mb", 0.0)
    if mb >= 0.01:
        return f"{mb:.1f} MB"
    pct = s.get("heap_pct", 0.0)
    if pct > 0:
        return f"~{pct:.1f}% of heap"
    return ""


class MATLeakSuspectsAnalyzer(MATBaseAnalyzer):
    """Analyses Eclipse MAT Leak Suspects ZIP reports."""

    def __init__(self, zip_path: str, output_dir: Optional[str] = None) -> None:
        super().__init__(zip_path, output_dir)
        self.report_data: Dict[str, Any] = {
            "summary": {
                "total_heap": "Unknown",
                "total_heap_mb": 0.0,
                "leak_suspects_count": 0,
                "total_leak_mb": 0.0,
                "heap_leak_pct": 0.0,
            },
            "primary_suspect": None,
            "significant_suspects": [],
            "other_suspects": [],
            "problems": [],
            "warnings": [],
        }
        self._raw_suspects: Dict[int, Dict[str, Any]] = {}

    # ── Parsing ──────────────────────────────────────────────────────────────

    def parse_report(self) -> None:
        if "index.html" in self.html_files:
            self._parse_index()

        for filename, entry in self.html_files.items():
            bare = Path(filename).name
            is_numeric = re.fullmatch(r"\d+\.html", bare)
            has_suspect = "Problem Suspect" in entry["content"]
            if (is_numeric or has_suspect) and bare not in ("index.html", "toc.html"):
                self._parse_suspect_page(filename)

        self._finalise_suspects()
        self._identify_problems()

    # ── index.html ───────────────────────────────────────────────────────────

    def _parse_index(self) -> None:
        """Extract total heap and high-level suspect sizes from index.html."""
        content = self.html_files["index.html"]["content"]
        soup = self.html_files["index.html"]["soup"]

        # ── Total heap extraction — four strategies in increasing generality ──
        # Strategy 1: alt= of the pie-chart <img> starting with "Pie chart"
        #   e.g. alt="Pie chart … Total: 107,347,272"
        total_m = re.search(
            r'alt="Pie chart[^"]*Total:\s*([\d,.]+)\s*(MB|GB|B)?"',
            content,
            re.IGNORECASE,
        )
        # Strategy 2: any alt= attribute containing "Total:" (different MAT versions
        #   may omit or reword the "Pie chart" prefix)
        if not total_m:
            total_m = re.search(
                r'alt="[^"]*Total:\s*([\d,.]+)\s*(MB|GB|B)?"',
                content,
                re.IGNORECASE,
            )
        # Strategy 3: bare "Total: X [unit?]" anywhere in the HTML
        #   Guard against trivially-small counts (e.g. "Total: 2 suspects")
        #   by requiring the value to be a large raw-bytes integer (≥1 MB)
        #   or carry an explicit size unit.
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
                # Accept: has a size unit, OR raw-bytes value large enough to be ≥1 MB
                if unit3 in ("MB", "GB", "B") or num3 >= 1_048_576:
                    total_m = m3
                    break

        if total_m:
            val_str = total_m.group(1)
            unit = (total_m.group(2) or "").upper()
            # Build a string that _parse_size_to_mb() understands
            raw = f"{val_str} {unit}".strip() if unit else val_str
            mb = self._parse_size_to_mb(raw)
            # Format a human-readable label
            heap_label = f"{mb:.1f} MB" if unit not in ("MB", "GB") else raw
            self.report_data["summary"]["total_heap"] = heap_label
            self.report_data["summary"]["total_heap_mb"] = mb

        # Suspect retained sizes from <area> alt= attributes (MAT emits alt=, NOT title=)
        # MAT may write units ("100 MB") or raw bytes ("104,889,144") — both handled.
        # Example alt values:
        #   "Slice (a)  Problem Suspect 1: Shallow Size: 40 B  Retained Size: 100.00 MB"
        #   "Slice (a)  Problem Suspect 1: Shallow Size: 40 B  Retained Size: 104,889,144"
        area_pat = re.compile(
            r'<area[^>]*\balt="[^"]*?Problem Suspect (\d+)[^"]*?'
            r'Retained Size:\s*([\d,.]+)\s*(MB|GB|B)?"',
            re.IGNORECASE,
        )
        total_from_areas = 0.0
        for m in area_pat.finditer(content):
            sid = int(m.group(1))
            val_str = m.group(2)
            unit = (m.group(3) or "").upper()
            # Delegate unit conversion to the shared helper
            parse_input = f"{val_str} {unit}".strip() if unit else val_str
            size_mb = self._parse_size_to_mb(parse_input)
            total_from_areas += size_mb
            suspect = self._get_or_create_suspect(sid)
            if suspect["retained_mb"] == 0:
                suspect["retained_mb"] = size_mb
                suspect["retained_raw"] = f"{size_mb:.1f} MB"

        if total_from_areas > 0:
            self.report_data["summary"]["total_leak_mb"] = total_from_areas

        # Short descriptions from "important" divs in index
        for div in soup.find_all("div", class_="important"):
            text = self._extract_text(div, max_len=500)
            num_m = re.search(r"Problem Suspect (\d+)", text)
            if num_m:
                sid = int(num_m.group(1))
                s = self._get_or_create_suspect(sid)
                if not s["description"]:
                    s["description"] = text
                # Extract class name from <q> tags inside the div
                q_tags = div.find_all("q")
                if q_tags and not s["class_name"]:
                    s["class_name"] = self._clean_text(q_tags[0].get_text())
                if len(q_tags) > 1 and not s["classloader"]:
                    s["classloader"] = self._clean_text(q_tags[1].get_text())
                # MAT description: "occupies 104,889,144 (98.19%) bytes"
                #   or (European locale): "occupy 564.224 (29,37 %) bytes"
                # _OCC_PAT handles both "occupy"/"occupies" and locale variants.
                occ_m = _OCC_PAT.search(text)
                if occ_m:
                    raw_bytes = _parse_occ_bytes(occ_m.group(1))
                    pct = _parse_occ_pct(occ_m.group(2))
                    s["heap_pct"] = pct
                    if s["retained_mb"] == 0:
                        s["retained_mb"] = raw_bytes / 1_048_576
                        s["retained_raw"] = f"{raw_bytes / 1_048_576:.1f} MB"
                else:
                    # Fallback: percentage alone (also handle European comma decimal)
                    pct_m = re.search(r"\(([\d.,]+)\s*%\)", text)
                    if pct_m:
                        s["heap_pct"] = _parse_occ_pct(pct_m.group(1))

        self.report_data["summary"]["leak_suspects_count"] = len(self._raw_suspects)

        # Strategy 4: if total heap is still unknown, derive it from the best
        # suspect's (retained_mb ÷ heap_pct).  The "occupies X (Y%)" text parsed
        # above gives us both values, so this works even when the img/alt tag is
        # absent or mis-formatted.
        if self.report_data["summary"]["total_heap_mb"] == 0.0:
            best_pct, best_mb = 0.0, 0.0
            for s in self._raw_suspects.values():
                pct = s.get("heap_pct", 0.0)
                mb = s.get("retained_mb", 0.0)
                if pct > best_pct and mb > 0:
                    best_pct, best_mb = pct, mb
            if best_pct > 0 and best_mb > 0:
                derived = best_mb / (best_pct / 100.0)
                self.report_data["summary"]["total_heap_mb"] = derived
                self.report_data["summary"]["total_heap"] = f"{derived:.1f} MB"
                logger.debug(
                    "Total heap derived from suspect pct: %.1f MB (pct=%.2f%%)",
                    derived,
                    best_pct,
                )

    # ── Suspect detail pages ─────────────────────────────────────────────────

    def _get_or_create_suspect(self, suspect_id: int) -> Dict[str, Any]:
        if suspect_id not in self._raw_suspects:
            self._raw_suspects[suspect_id] = {
                "id": suspect_id,
                "title": f"Problem Suspect {suspect_id}",
                "class_name": "",
                "description": "",
                "retained_mb": 0.0,
                "retained_raw": "",
                "heap_pct": 0.0,
                "thread": None,
                "classloader": None,
                "stack": [],
                "key_objects": [],
            }
        return self._raw_suspects[suspect_id]

    def _parse_suspect_page(self, filename: str) -> None:
        """Enrich a suspect record from its detail HTML page."""
        entry = self.html_files[filename]
        content = entry["content"]
        soup = entry["soup"]

        # Determine suspect id
        id_match = re.search(r"Problem Suspect (\d+)", content)
        if id_match:
            sid = int(id_match.group(1))
        else:
            num_m = re.search(r"(\d+)", Path(filename).stem)
            sid = int(num_m.group(1)) if num_m else 0

        suspect = self._get_or_create_suspect(sid)

        # ── Description and class name from "important" div ──────────────────
        important = soup.find("div", class_="important")
        if important:
            if not suspect["description"]:
                suspect["description"] = self._extract_text(important, max_len=500)
            # Extract class name and classloader from <q> tags (most reliable)
            q_tags = important.find_all("q")
            if q_tags and not suspect["class_name"]:
                suspect["class_name"] = self._clean_text(q_tags[0].get_text())
            if len(q_tags) > 1 and not suspect["classloader"]:
                suspect["classloader"] = self._clean_text(q_tags[1].get_text())
            # "occupies 104,889,144 (98.19%) bytes" → retained MB + heap %
            # European locale variant: "occupy 564.224 (29,37 %) bytes"
            occ_m = _OCC_PAT.search(suspect["description"])
            if occ_m:
                raw_bytes = _parse_occ_bytes(occ_m.group(1))
                suspect["heap_pct"] = _parse_occ_pct(occ_m.group(2))
                if suspect["retained_mb"] == 0:
                    suspect["retained_mb"] = raw_bytes / 1_048_576
                    suspect["retained_raw"] = f"{raw_bytes / 1_048_576:.1f} MB"
            elif not suspect["heap_pct"]:
                pct_m = re.search(r"\(([\d.,]+)\s*%\)", suspect["description"])
                if pct_m:
                    suspect["heap_pct"] = _parse_occ_pct(pct_m.group(1))
        elif not suspect["description"]:
            for para in soup.find_all("p"):
                txt = self._extract_text(para, max_len=400)
                if len(txt) > 50 and "copyright" not in txt.lower():
                    suspect["description"] = txt
                    break

        # ── Thread ──────────────────────────────────────────────────────────
        if not suspect["thread"]:
            for pat in (
                r"(Thread[^@]*@\s*0x[0-9a-f]+[^\s<]*)",
                r"thread\s+([^\n<]{5,80})",
            ):
                m = re.search(pat, content, re.IGNORECASE)
                if m:
                    suspect["thread"] = self._clean_text(m.group(1))
                    break

        # ── Class loader (fallback if not extracted from <q>) ────────────────
        if not suspect["classloader"]:
            for pat in (
                r"loaded by.*?&lt;(system class loader)&gt;",
                r"loaded by.*?([A-Za-z0-9_$.]+(?:ClassLoader|Loader)[^\s<]*)",
                r"Context Class Loader[^>]*>([^<]+)",
            ):
                m = re.search(pat, content, re.IGNORECASE | re.DOTALL)
                if m:
                    suspect["classloader"] = self._clean_text(m.group(1))
                    break

        # ── Stack trace ──────────────────────────────────────────────────────
        if not suspect["stack"]:
            suspect["stack"] = self._extract_stack_trace(soup)

        # ── Key objects from first matching data table ───────────────────────
        if not suspect["key_objects"]:
            for table in soup.find_all("table"):
                headers = table.find("tr")
                if not headers:
                    continue
                header_text = headers.get_text().lower()
                if "class" in header_text and (
                    "shallow" in header_text or "retained" in header_text
                ):
                    objects: List[str] = []
                    for row in table.find_all("tr")[1:6]:
                        cols = row.find_all(["td", "th"])
                        if len(cols) >= 2:
                            obj_name = self._clean_text(
                                cols[0].get_text(separator=" ")
                            )
                            obj_size = cols[1].get_text(strip=True)
                            if obj_name and len(obj_name) > 3:
                                # obj_size may be raw bytes — convert for display
                                mb = self._parse_size_to_mb(obj_size)
                                display = (
                                    f"{mb:.1f} MB"
                                    if mb >= 0.1
                                    else (obj_size or "?")
                                )
                                objects.append(f"{obj_name}: {display}")
                    if objects:
                        suspect["key_objects"] = objects
                        break

        # ── Retained size fallback ───────────────────────────────────────────
        # Try patterns that include a unit first, then raw-bytes fallbacks.
        if not suspect["retained_raw"]:
            # 1. "occup(ies|y) RAW_BYTES (PCT%)" — handles both en/EU locales
            occ_m = _OCC_PAT.search(content)
            if occ_m:
                raw_bytes = _parse_occ_bytes(occ_m.group(1))
                suspect["retained_mb"] = raw_bytes / 1_048_576
                suspect["retained_raw"] = f"{suspect['retained_mb']:.1f} MB"
                if not suspect["heap_pct"]:
                    suspect["heap_pct"] = _parse_occ_pct(occ_m.group(2))
            else:
                # 2. Patterns with explicit unit
                for pat in (
                    r"Retained\s+Heap[:\s]+([\d,.]+)\s*(MB|GB)",
                    r"retains\s+([\d,.]+)\s*(MB|GB)",
                    r"([\d,.]+)\s*(MB|GB)\s+of\s+memory",
                    r"Retained Size:\s*([\d,.]+)\s*(MB|GB)",
                ):
                    m = re.search(pat, content, re.IGNORECASE)
                    if m:
                        val_str = m.group(1).replace(",", "")
                        unit = m.group(2)
                        suspect["retained_raw"] = f"{val_str} {unit}"
                        suspect["retained_mb"] = (
                            float(val_str) * 1024 if unit.upper() == "GB" else float(val_str)
                        )
                        break

    def _extract_stack_trace(self, soup) -> List[str]:
        frames: List[str] = []
        seen: set = set()

        def _add(line: str) -> None:
            line = self._clean_text(line)
            if line and line not in seen:
                seen.add(line)
                frames.append(line)

        for pre in soup.find_all("pre"):
            for line in pre.get_text().splitlines():
                line = line.strip()
                if line and ("Thread" in line or "at " in line or "java." in line):
                    _add(line)

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                txt = row.get_text()
                if "at " in txt and any(
                    pkg in txt for pkg in ("java.", "com.", "org.", "net.")
                ):
                    for line in txt.splitlines():
                        if "at " in line:
                            _add(line.strip())

        return frames[:10]

    # ── Finalisation ─────────────────────────────────────────────────────────

    def _finalise_suspects(self) -> None:
        all_suspects = sorted(
            self._raw_suspects.values(),
            key=lambda s: s.get("retained_mb", 0),
            reverse=True,
        )

        self.report_data["summary"]["leak_suspects_count"] = len(all_suspects)
        if self.report_data["summary"]["total_leak_mb"] == 0.0:
            self.report_data["summary"]["total_leak_mb"] = sum(
                s.get("retained_mb", 0) for s in all_suspects
            )

        total_heap_mb = self.report_data["summary"]["total_heap_mb"]
        total_leak_mb = self.report_data["summary"]["total_leak_mb"]

        # Last-resort derivation: if _parse_index() couldn't determine total heap
        # (because the img/alt tag was absent), try again now that suspect pages
        # have been parsed and heap_pct values may have been enriched.
        if total_heap_mb == 0.0:
            best_pct, best_mb = 0.0, 0.0
            for s in all_suspects:
                pct = s.get("heap_pct", 0.0)
                mb = s.get("retained_mb", 0.0)
                if pct > best_pct and mb > 0:
                    best_pct, best_mb = pct, mb
            if best_pct > 0 and best_mb > 0:
                total_heap_mb = best_mb / (best_pct / 100.0)
                self.report_data["summary"]["total_heap_mb"] = total_heap_mb
                self.report_data["summary"]["total_heap"] = f"{total_heap_mb:.1f} MB"

        if total_heap_mb > 0:
            self.report_data["summary"]["heap_leak_pct"] = round(
                total_leak_mb / total_heap_mb * 100, 1
            )

        if not all_suspects:
            return

        self.report_data["primary_suspect"] = all_suspects[0]
        primary_mb = all_suspects[0].get("retained_mb", 0)

        significant, others = [], []
        for s in all_suspects[1:]:
            mb = s.get("retained_mb", 0)
            if mb > 50 or (primary_mb > 0 and mb / primary_mb > 0.2):
                significant.append(s)
            else:
                others.append(s)

        self.report_data["significant_suspects"] = significant
        self.report_data["other_suspects"] = others

    def _identify_problems(self) -> None:
        problems: List[Dict] = []
        warnings: List[Dict] = []

        primary = self.report_data["primary_suspect"]
        if primary:
            mb = primary.get("retained_mb", 0)
            sev = "HIGH" if mb > 500 else "MEDIUM"
            cls = primary.get("class_name") or "Unknown class"
            size_str = _size_label(primary)
            problems.append(
                {
                    "severity": sev,
                    "type": "PRIMARY_LEAK",
                    "description": (
                        f"Primary leak suspect: {cls} retains {size_str}"
                        if size_str else
                        f"Primary leak suspect: {cls}"
                    ),
                    "details": primary.get("description", ""),
                }
            )

        pct = self.report_data["summary"]["heap_leak_pct"]
        if pct > 70:
            problems.append(
                {
                    "severity": "HIGH",
                    "type": "SIGNIFICANT_LEAK_RATIO",
                    "description": (
                        f"Leak suspects account for {pct:.1f}% of total heap — "
                        "heap will exhaust soon"
                    ),
                }
            )
        elif pct > 40:
            warnings.append(
                {
                    "type": "ELEVATED_LEAK_RATIO",
                    "description": f"Leak suspects occupy {pct:.1f}% of total heap",
                }
            )

        # Every suspect MAT flagged is a real finding — report them all.
        # significant_suspects are already above size thresholds; other_suspects
        # are smaller but were still identified by MAT as Problem Suspects.
        all_secondary = (
            self.report_data["significant_suspects"]
            + self.report_data["other_suspects"]
        )
        for s in all_secondary:
            mb = s.get("retained_mb", 0)
            cls = s.get("class_name") or f"Suspect {s['id']}"
            size_str = _size_label(s)
            # Severity: HIGH if large, MEDIUM if significant, LOW if minor
            if mb > 200:
                sev = "HIGH"
            elif s in self.report_data["significant_suspects"]:
                sev = "MEDIUM"
            else:
                sev = "LOW"
            problems.append(
                {
                    "severity": sev,
                    "type": "SECONDARY_LEAK",
                    "description": (
                        f"Leak suspect {s['id']}: {cls} retains {size_str}"
                        if size_str else
                        f"Leak suspect {s['id']}: {cls}"
                    ),
                    "details": s.get("description", ""),
                }
            )

        self.report_data["problems"] = problems
        self.report_data["warnings"] = warnings

    # ── Report generation ─────────────────────────────────────────────────────

    def generate_report(self) -> str:
        W = _W
        lines: List[str] = []

        s = self.report_data["summary"]
        pct = s["heap_leak_pct"]
        n_suspects = s["leak_suspects_count"]

        # ── Banner ───────────────────────────────────────────────────────────
        lines.append(_banner("MEMORY LEAK SUSPECTS REPORT", "Eclipse MAT Analysis"))
        lines.append("")

        # ── Heap overview ─────────────────────────────────────────────────────
        lines.append(_section("HEAP OVERVIEW"))
        lines.append("")
        lines.append(f"  Total Heap Size  :  {s['total_heap']}")
        lines.append(f"  Leak Suspects    :  {n_suspects}")
        lines.append(f"  Leaked Memory    :  {s['total_leak_mb']:.1f} MB")
        if pct > 0:
            lines.append(f"  Heap Consumed    :  {_bar(pct, 30)}")
        lines.append("")

        # ── Problems summary ──────────────────────────────────────────────────
        probs = self.report_data["problems"]
        warns = self.report_data["warnings"]

        if probs:
            lines.append(_section(f"⚠  ISSUES DETECTED  ({len(probs)})"))
            lines.append("")
            for p in probs:
                icon = _severity_icon(p["severity"])
                lines.append(f"  {icon}  [{p['severity']}]  {p['description']}")
            lines.append("")

        if warns:
            lines.append(_section("WARNINGS"))
            lines.append("")
            for w in warns:
                lines.append(f"  🟡  {w['description']}")
            lines.append("")

        # ── Primary suspect ───────────────────────────────────────────────────
        primary = self.report_data["primary_suspect"]
        if primary:
            lines.append("╔" + "═" * (W - 2) + "╗")
            title = "  PRIMARY LEAK SUSPECT"
            lines.append("║" + title.ljust(W - 2) + "║")
            lines.append("╚" + "═" * (W - 2) + "╝")
            lines.append("")

            retained = (
                primary.get("retained_raw") or f"{primary.get('retained_mb', 0):.1f} MB"
            )
            cls = primary.get("class_name") or "Unknown"
            heap_pct = primary.get("heap_pct", 0)

            lines.append(f"  Class         :  {cls}")
            lines.append(f"  Retained Heap :  {retained}")
            if heap_pct > 0:
                lines.append(f"  Heap Share    :  {_bar(heap_pct, 30)}")
            if primary.get("classloader"):
                lines.append(f"  ClassLoader   :  {primary['classloader']}")
            if primary.get("thread"):
                lines.append(f"  Thread        :  {primary['thread']}")
            lines.append("")

            if primary.get("description"):
                lines.append("  Description:")
                # word-wrap at W-6
                words = primary["description"].split()
                buf: List[str] = []
                for w in words:
                    if sum(len(x) + 1 for x in buf) + len(w) > W - 6:
                        lines.append("    " + " ".join(buf))
                        buf = [w]
                    else:
                        buf.append(w)
                if buf:
                    lines.append("    " + " ".join(buf))
                lines.append("")

            if primary.get("stack"):
                lines.append("  Stack Trace (top frames):")
                for frame in primary["stack"][:6]:
                    lines.append(f"    {frame}")
                lines.append("")

            if primary.get("key_objects"):
                lines.append("  Key Objects:")
                for obj in primary["key_objects"][:5]:
                    lines.append(f"    • {obj}")
                lines.append("")

        # ── Significant suspects ──────────────────────────────────────────────
        sig = self.report_data["significant_suspects"]
        if sig:
            lines.append(_section("OTHER SIGNIFICANT SUSPECTS"))
            lines.append("")
            for s_item in sig[:5]:
                size = s_item.get("retained_raw") or f"{s_item.get('retained_mb', 0):.1f} MB"
                cls = s_item.get("class_name") or s_item["title"]
                heap_pct = s_item.get("heap_pct", 0)
                sev = "HIGH" if s_item.get("retained_mb", 0) > 200 else "MEDIUM"
                icon = _severity_icon(sev)
                lines.append(f"  {icon}  {cls}")
                lines.append(f"       Retained: {size}" + (f"  ({heap_pct:.1f}% of heap)" if heap_pct else ""))
                if s_item.get("thread"):
                    lines.append(f"       Thread:   {s_item['thread']}")
                if s_item.get("description"):
                    desc = s_item["description"][:120].replace("\n", " ")
                    lines.append(f"       Details:  {desc}…" if len(s_item["description"]) > 120 else f"       Details:  {desc}")
                lines.append("")
            if len(sig) > 5:
                lines.append(f"  … and {len(sig) - 5} more significant suspect(s).")
                lines.append("")

        # ── Minor suspects ────────────────────────────────────────────────────
        others = self.report_data["other_suspects"]
        if others:
            lines.append(_section("MINOR SUSPECTS"))
            lines.append("")
            lines.append(f"  {len(others)} smaller suspect(s) each retaining < 50 MB.")
            for o in others[:5]:
                size = o.get("retained_raw") or f"{o.get('retained_mb', 0):.1f} MB"
                cls = o.get("class_name") or o["title"]
                lines.append(f"    • {cls}: {size}")
            lines.append("")

        # ── Recommendations ───────────────────────────────────────────────────
        lines.append(_section("IMMEDIATE ACTION PLAN"))
        lines.append("")
        step = 1

        if primary:
            cls = primary.get("class_name") or "the primary suspect class"
            lines.append(f"  {step}. Investigate {cls}:")
            step += 1
            if primary.get("classloader"):
                lines.append(f"       • ClassLoader: {primary['classloader']}")
            if primary.get("stack"):
                lines.append("       • Examine these stack frames:")
                for frame in primary["stack"][:3]:
                    lines.append(f"         {frame}")
            lines.append("       • Find all static references holding objects of this class.")
            lines.append("       • Check whether instances are accumulated in a list/map/cache.")
            lines.append("")

        if sig:
            lines.append(f"  {step}. Address {len(sig)} other significant suspect(s):")
            step += 1
            lines.append("       • Look for common root cause (shared collection, listener, etc.).")
            lines.append("       • Use OQL in MAT: SELECT * FROM <ClassName> to enumerate instances.")
            lines.append("")

        lines.append(f"  {step}. Capture multiple heap dumps over time:")
        step += 1
        lines.append("       • Compare object counts between dumps to isolate growing sets.")
        lines.append("       • Focus on classes whose count grows monotonically.")
        lines.append("")

        lines.append(f"  {step}. Enable automatic heap dumps on OOM in production:")
        lines.append("       -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/var/dumps/")
        lines.append("")

        # ── Full recommendation engine from base class ─────────────────────
        lines.append(self.build_summary())

        return "\n".join(lines)
