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

def _load_java_recommendations() -> Dict[str, Dict[str, Any]]:
    """Load recommendations from external JSON file."""
    recs_path = Path(__file__).parent / "java_recommendations.json"
    with recs_path.open("r", encoding="utf-8") as f:
        return json.load(f)


JAVA_RECOMMENDATIONS: Dict[str, Dict[str, Any]] = _load_java_recommendations()


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
