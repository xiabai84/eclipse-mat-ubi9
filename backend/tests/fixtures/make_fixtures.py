#!/usr/bin/env python3
"""Reduce real Eclipse MAT report ZIPs to test fixtures.

Usage: make_fixtures.py <dir with <name>_Leak_Suspects.zip etc.> <name> <fixture name>

Writes the HTML pages the analyzers read to mat/<fixture>_<report>/ (text, no binaries - conftest zips them at
test time). Drops the System Properties page (it describes the machine that ran the dump) and replaces absolute
paths and the user and host name, so the fixtures contain no local data.
"""

import getpass
import re
import socket
import sys
import zipfile
from pathlib import Path

KEEP = {
    "Leak_Suspects": lambda name, html: name == "index.html" or "<title>Problem Suspect" in html,
    "System_Overview": lambda name, html: name == "index.html"
        or re.search(r"(Class_Histogram|Thread_Overview|Top_Consumers)\d*\.html$", name) is not None,
    "Top_Components": lambda name, html: name == "index.html"
        or re.search(r"\(\d+%\)</title>", html) is not None or "<title>Top Consumers" in html,
}
SCRUB = [
    (re.compile(r"(/private|/Users|/home|/var/folders|/tmp)(/[\w.@%+-]+)+"), "/data/dump.hprof"),
    # MAT prints String contents next to object references, e.g. a static USER_NAME field: the user and host
    # name of the machine that made the dump
    (re.compile(re.escape(getpass.getuser())), "user"),
    (re.compile(re.escape(socket.gethostname().split(".")[0]), re.IGNORECASE), "host"),
]


def reduce(src: Path, dest: Path, kind: str) -> None:
    with zipfile.ZipFile(src) as zin:
        for info in zin.infolist():
            if not info.filename.endswith(".html"):
                continue
            html = zin.read(info).decode("utf-8")
            if not KEEP[kind](Path(info.filename).name, html):
                continue
            for pattern, repl in SCRUB:
                html = pattern.sub(repl, html)
            target = dest / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")


def main() -> None:
    src_dir, name, fixture = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    out = Path(__file__).parent / "mat"
    out.mkdir(exist_ok=True)
    for kind in KEEP:
        reduce(src_dir / f"{name}_{kind}.zip", out / f"{fixture}_{kind}", kind)
        print(out / f"{fixture}_{kind}")


if __name__ == "__main__":
    main()
