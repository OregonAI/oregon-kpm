#!/usr/bin/env python3
"""Second-engine corroboration for OCR'd scans, per the platform's two-engine rule.

The rule lives in `oregon-policy-repo/AGENTS.md` and the reference implementation is that
repo's `src/ocr_fallback_eo.py`. This module is the same contract for this corpus: same
0.80 agreement bar, same quality gate, same `conversion_notes` wording, different engine
pair. It exists rather than being imported because the two corpora share no code path --
that repo promotes a stub to verbatim, this one ingests a document from scratch.

WHY A SECOND ENGINE AT ALL. OCR of these scans is good but not clean -- the DOGAMI 2017
report yields "pernitted rrine sites" for "permitted mine sites" -- and mostly-right text is
the dangerous case, because it reads as authoritative. One engine's output is unverifiable:
there is nothing to check it against. Two engines that share no model weights are
vanishingly unlikely to invent the SAME words, so high agreement is positive evidence the
words are physically on the page. That evidence, not a better engine, is what makes
promotion defensible.

THE PAIR IS tesseract + PaddleOCR, MEASURED NOT ASSUMED. Across the six scans in this
corpus, agreement on the word sequence:

    appr-oprd-2022-08-15     0.929        appr-racing-2022        0.921
    appr-odva-2023-9-26      0.928        appr-dogami-09-26-2017  0.910
    appr-ccb-2019-10-02      0.874        2016-dogami-kpm-report  0.816

docTR (DBNet + CRNN) was measured as a third engine and is the TIEBREAKER rather than the
default, for two reasons worth keeping. It agrees with tesseract less than Paddle does on
every document (0.747-0.862), so it would lower every score. But it was also the only engine
that read appr-oprd-2022-08-15 -- a scan that is 180 over -- correctly with NO
document-specific retry, because it straightens pages itself. Tesseract needs
`--rotate-pages-threshold 0` on that document and Paddle needs orientation classification
switched on; forget either and the engine returns fluent nonsense that still passes a length
check. So docTR is the engine to reach for when a document disagrees, not the one to drop.

WHAT AGREEMENT DOES NOT PROVE. It is evidence the words are on the page. It is NOT evidence
they were read correctly, and it says nothing about figures -- two engines can misread the
same smudged digit the same way. Every number in an OCR'd document stays unverified against
the source, which is why the body says so and `conversion_notes` ends "NOT human-verified".
"""
from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "_meta" / "snapshots"

MIN_AGREEMENT = 0.80
MIN_DICT_RATIO = 0.80
MIN_WORDS = 100

ENGINES = ("tesseract (ocrmypdf)", "paddleocr PP-OCRv6")

WORD = re.compile(r"[a-z]{2,}")
# A reported figure: percentage, currency, count, ratio. Matched loosely on purpose -- the
# question is whether the two engines read the same characters, not whether the result is a
# well-formed number.
FIGURE = re.compile(r"\$?-?\d[\d,]*(?:\.\d+)?%?")


def vocabulary() -> set[str]:
    """Dictionary for the quality gate, built from THIS corpus's own non-OCR text.

    `/usr/share/dict/words` is absent on this host and on the CI runner, and a general
    English wordlist is a poor fit for agency performance prose anyway -- "biennially",
    "lidar", "recidivism" and every agency acronym would count against a document for being
    domain vocabulary. The snapshots whose text came from the PDF's own text layer are the
    right reference: same register, same corpus, and none of them are OCR output, so the
    dictionary cannot be contaminated by the errors it is meant to detect.

    Sampled evenly across the sorted set rather than taking the first N, for the reason the
    reference implementation records: an arbitrary slice skews the vocabulary toward
    whichever agencies sort first and moves ratios by several points.
    """
    # OCR'd SNAPSHOTS ARE EXCLUDED, and the exclusion is the whole point rather than
    # housekeeping. Globbing every .txt puts the OCR output INTO the dictionary that judges
    # it: "pernitted" and "rrine" become recognised vocabulary, and every OCR'd document
    # then scores 100% dictionary-recognizable no matter how badly it was read. That was the
    # first measured result here -- six documents, 100% each -- and it is a gate that cannot
    # fail, which is worse than no gate because it looks like evidence.
    ocr_ids = set()
    for md in (ROOT / "reports").glob("*.md"):
        head = md.read_text(encoding="utf-8", errors="replace").split("---", 2)
        if len(head) >= 3 and re.search(r"^text_source:\s*ocr\s*$", head[1], re.M):
            ocr_ids.add(md.stem)
    files = sorted(p for p in SNAPSHOTS.glob("*.txt") if p.stem not in ocr_ids)
    step = max(1, len(files) // 400)
    vocab: set[str] = set()
    for p in files[::step]:
        vocab |= set(WORD.findall(p.read_text(encoding="utf-8", errors="replace").lower()))
    return vocab


def paddle_text(pdf_path: Path, workdir: Path) -> str | None:
    """PaddleOCR over the ORIGINAL scan. None if PaddleOCR is unavailable.

    Reads the original rather than tesseract's output PDF, so the two engines share nothing
    but the pixels -- corroboration against a copy of the other engine's reading would be an
    echo, not evidence.

    Orientation classification is ON. With it off, Paddle read appr-oprd-2022-08-15 upside
    down and scored 0.050 against tesseract; with it on, 0.929. That number measured the
    configuration, not the page, and it is exactly the kind of false negative that would send
    a recoverable document to the reject pile.
    """
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        return None
    workdir.mkdir(parents=True, exist_ok=True)
    for old in workdir.glob("*.png"):
        old.unlink()
    try:
        subprocess.run(["pdftoppm", "-r", "200", "-png", str(pdf_path), str(workdir / "p")],
                       check=True, capture_output=True, timeout=1800)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    ocr = PaddleOCR(lang="en", use_doc_orientation_classify=True,
                    use_doc_unwarping=False, use_textline_orientation=True)
    out: list[str] = []
    for img in sorted(workdir.glob("p-*.png")):
        for d in ocr.predict(str(img)):
            out.extend(d.get("rec_texts") or [])
    return "\n".join(out)


def score(primary: str, cross_check: str, vocab: set[str]) -> dict:
    """Quality of the text that would be committed, and its agreement with the second engine."""
    wa = WORD.findall(primary.lower())
    wb = WORD.findall(cross_check.lower())
    ratio = sum(1 for w in wa if w in vocab) / len(wa) if wa else 0.0
    agreement = (difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio()
                 if wa and wb else 0.0)
    # Wide-tracked letterhead and all-caps headings get split per glyph-cluster by the
    # detector and rejoined without spaces. COUNTED SO IT CAN BE DISCLOSED, deliberately not
    # repaired: re-inserting word boundaries would be writing text the OCR did not resolve.
    glued = len(re.findall(r"\b[A-Za-z]{18,}\b", primary))
    # AGREEMENT ON THE FIGURES, REPORTED SEPARATELY BECAUSE THE HEADLINE NUMBER HIDES IT.
    # The reference metric counts `[a-z]{2,}` and therefore excludes every digit. For a
    # corpus of executive orders that is reasonable -- the payload is prose. Here the payload
    # is targets and actuals, and measured on these six documents the figure-inclusive score
    # runs 3-9 points BELOW the word-only score, because digits are exactly where two OCR
    # engines diverge. Publishing only the word-only number would overstate agreement about
    # the part of the document a reader actually cites.
    #
    # It is disclosed, not gated: a low figure score should send a document to human review,
    # and there is no evidence yet for where that threshold belongs.
    fa = FIGURE.findall(primary.lower())
    fb = FIGURE.findall(cross_check.lower())
    fig = (difflib.SequenceMatcher(None, fa, fb, autojunk=False).ratio()
           if fa and fb else 0.0)
    return {"words": len(wa), "dict_ratio": ratio, "agreement": agreement, "glued": glued,
            "figures": len(fa), "figure_agreement": fig,
            "gate_ok": len(wa) >= MIN_WORDS and ratio >= MIN_DICT_RATIO,
            "agree_ok": agreement >= MIN_AGREEMENT}


def notes(s: dict) -> str:
    """The `conversion_notes` string. Wording matches the reference implementation."""
    glued_note = (f"; {s['glued']} heading/letterhead token(s) lost their word spacing in "
                  f"extraction and are left as-is rather than reconstructed"
                  if s["glued"] else "")
    return (f"no text layer in the source PDF; text recovered by OCR. Two independent "
            f"engines ({' + '.join(ENGINES)}) agree on {s['agreement']:.0%} of the word "
            f"sequence and {s['figure_agreement']:.0%} of the {s['figures']} figures, "
            f"{s['dict_ratio']:.0%} dictionary-recognizable{glued_note}; "
            f"NOT human-verified — treat every number as unchecked against the source")
