"""Writes THIRD-PARTY-NOTICES.txt for the Windows build: every package bundled into Horu.exe,
with its licence text, plus the things that aren't Python packages (the OCR models, the word
dictionary, fonts). Run by packaging/build.ps1 with the build environment's Python:

    python packaging/notices.py <requirements.txt> <out.txt>

`requirements.txt` is what `uv export` wrote for the build: only packages named there are
listed (not PyInstaller and its helpers, which build the app but aren't part of it).
"""

from __future__ import annotations

import importlib.metadata as metadata
import re
import sys
from pathlib import Path

HEADER = """Horu: third-party notices
=========================

Horu itself is MIT licensed (see LICENSE.txt). Horu.exe is built with PyInstaller and bundles
the software and data listed here, each under its own licence. Horu's source code, with the
exact versions used (uv.lock) and the script that builds this app (packaging/build.ps1), is at
https://github.com/LukasRuprecht/Horu

Not Python packages
-------------------

Python (the interpreter and standard library, bundled)
  Python Software Foundation License Version 2. https://docs.python.org/3/license.html

OCR models (bundled in _internal/rapidocr/models)
  PP-OCR text detection and recognition models from PaddleOCR, converted to ONNX by the
  RapidOCR project. Apache License 2.0.
  https://github.com/PaddlePaddle/PaddleOCR  https://github.com/RapidAI/RapidOCR

Word dictionary for analysis (bundled as the SudachiDict-full package, listed below)
  SudachiDict by Works Applications Co., Ltd., Apache License 2.0. It is built from UniDic
  (BSD licence, The UniDic Consortium) and mecab-ipadic-NEologd (Apache License 2.0); see its
  licence texts below.

Fonts (bundled in _internal/horu/ui/fonts, with their licence texts beside them)
  DM Sans and Noto Sans JP, SIL Open Font License 1.1.

JLPT levels (bundled as _internal/horu/resources/jlpt.tsv)
  From open-anki-jlpt-decks (MIT, https://github.com/jamsinclair/open-anki-jlpt-decks), based
  on Jonathan Waller's JLPT lists (https://www.tanos.co.uk/jlpt/, CC BY).

Dictionaries downloaded on first start (not bundled)
  JMdict and JMnedict, property of the Electronic Dictionary Research and Development Group,
  used under CC BY-SA 4.0. https://www.edrdg.org/edrdg/licence.html

Microsoft Visual C++ runtime (bundled DLLs)
  Redistributed under the Microsoft Visual Studio redistributable terms.

LGPL packages
-------------

pystray and pynput are under the GNU Lesser General Public License v3 and are bundled
unmodified. You may replace them with your own builds: get Horu's source at the address
above, change the versions, and run packaging/build.ps1. The LGPL and GPL texts are with
those packages below.

Python packages
---------------
"""

_LICENCE_FILE = re.compile(r"(^|/)(licen[cs]e|copying|notice|authors|copyright|thirdpartynotices)[^/]*$", re.IGNORECASE)

_BSD_3 = """Redistribution and use in source and binary forms, with or without modification, are
permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of
   conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice, this list of
   conditions and the following disclaimer in the documentation and/or other materials
   provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors may be used to
   endorse or promote products derived from this software without specific prior written
   permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR
IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE."""

_APACHE = "Licensed under the Apache License, Version 2.0: its full text is in the appendix at the end of this file."

# Packages whose wheels carry no licence file: what their project pages and sources state.
_STATED = {
    "antlr4-python3-runtime": "Copyright (c) 2012-2017 The ANTLR Project. All rights reserved.\n\n" + _BSD_3,
    "flatbuffers": "Copyright Google Inc.\n" + _APACHE,
    "rapidocr": "Copyright RapidAI.\n" + _APACHE,
    "proxy_tools": "Copyright (c) 2013 by Armin Ronacher (adapted by Jonathan Tushman 2014). Extracted from "
    "Werkzeug, which is under the 3-clause BSD licence; the package itself is published as MIT.\n\n" + _BSD_3,
}


def names(requirements: Path) -> list[str]:
    found = []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(==|@|;|$)", line.strip())
        if match:
            found.append(match.group(1))
    return sorted(set(found), key=str.lower)


def entry(name: str) -> str | None:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:  # not for this platform (a Linux-only dependency)
        return None
    meta = dist.metadata
    licence = meta.get("License-Expression") or ""
    if not licence:
        classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
        licence = "; ".join(classifiers) or (meta.get("License") or "").strip().split("\n")[0][:80] or "see the text below"
    urls = [u.split(",", 1)[1].strip() for u in meta.get_all("Project-URL") or [] if "," in u]
    home = meta.get("Home-page") or (urls[0] if urls else "")
    out = [f"\n{'=' * 78}\n{meta['Name']} {dist.version}\nLicence: {licence}" + (f"\n{home}" if home else "") + f"\n{'=' * 78}\n"]
    texts = 0
    for file in dist.files or []:
        path = str(file).replace("\\", "/")
        # In the package's metadata folder, or at the top of the package itself (onnxruntime).
        placed = ".dist-info/" in path or path.count("/") == 1
        if placed and _LICENCE_FILE.search(path) and not path.endswith((".py", ".pyc", ".pyi")):
            try:
                text = Path(dist.locate_file(file)).read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            out.append(f"--- {path.split('.dist-info/', 1)[-1]} ---\n{text}\n")
            texts += 1
    if not texts:
        stated = _STATED.get(meta["Name"]) or _STATED.get(meta["Name"].replace("_", "-"))
        out.append((stated or "(This package ships no licence file; see its project page for the full text.)") + "\n")
    return "\n".join(out)


def apache_text() -> str:
    """The Apache License 2.0, from a bundled package that ships it."""
    for name in ("requests", "openai", "SudachiPy"):
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        for file in dist.files or []:
            path = str(file).replace("\\", "/")
            if ".dist-info/" in path and _LICENCE_FILE.search(path):
                text = Path(dist.locate_file(file)).read_text(encoding="utf-8", errors="replace")
                if "Apache License" in text and "TERMS AND CONDITIONS" in text:
                    return text.strip()
    return "https://www.apache.org/licenses/LICENSE-2.0.txt"


def main() -> None:
    requirements, target = Path(sys.argv[1]), Path(sys.argv[2])
    entries = [e for e in (entry(n) for n in names(requirements)) if e]
    appendix = f"\n\n{'=' * 78}\nAppendix: Apache License, Version 2.0\n{'=' * 78}\n\n{apache_text()}\n"
    target.write_text(HEADER + "".join(entries) + appendix, encoding="utf-8")
    print(f"Wrote {target}: {len(entries)} packages")


if __name__ == "__main__":
    main()
