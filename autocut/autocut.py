#!/usr/bin/env python3
"""Propose a footnote crop line for every page of a scanned book.

Writes the same ``CoordinateData/<page>.txt`` files that the FootnoteCrop WPF app
writes (one number per file: the y-pixel to keep everything above), so the app can be
used afterwards as a review pass and the existing ImageMagick step works unchanged.

How it works (no AI model involved):

1. Get the position and size of every text line on every page, either from
   Tesseract OCR (page images) or from the text layer of a PDF.
2. Work out the book's body-text size and footnote size from all pages together.
3. On each page, find the single split line that best separates "body-size" lines (top)
   from "smaller" lines (bottom). The crop goes in the gap just under the last body line.
4. Flag pages that look unusual so they get a human look.

Only the Python standard library is needed, plus the ``tesseract`` command (image
mode) and ``pdftoppm`` / ``pdftotext`` from poppler (PDF mode).
"""

import argparse
import csv
import json
import math
import os
import re
import statistics
import subprocess
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional

# Same lookup order as FootnoteCrop.WPF/MainWindow.xaml.cs.
SOURCE_FOLDERS = ("VerticalCropped", "TopCropped", "Straight", "Cropped")

HEADER_FRACTION = 0.09  # ignore running heads / page numbers at the top of the page
MIN_LINE_WIDTH_FRACTION = 0.025  # ignore specks and stray marks narrower than this
WIDE_LINE_FRACTION = 0.25  # lines this wide are used to learn the body size
MIN_WIDE_LINES = 3  # fewer than this and the page is treated as blank / title / image
SIZE_BIN_RATIO = 1.04  # line sizes within ~4% are "the same size"
NARROW_NOTES_FRACTION = 0.35  # footnote lines are wide; a median narrower than this is suspicious
QUOTE_INDENT_FRACTION = 0.03  # a block indented at least this much (of page width) may be a quote
MIXED_MIN_LINES = 3  # a page is "mixed" only if at least this many lines disagree with the split
MIXED_MIN_FRACTION = 0.08  # ... and at least this share of its lines (OCR noise hits a line or two)
BODY_DEVIATION = 0.08  # page body size this far from the book's body size gets flagged


@dataclass
class Line:
    top: float
    bottom: float
    left: float
    right: float
    size: float  # font-size proxy; only ever compared with other sizes from the same source

    @property
    def width(self):
        return self.right - self.left


@dataclass
class Page:
    name: str
    width: float
    height: float
    lines: List[Line] = field(default_factory=list)


@dataclass
class Result:
    name: str
    status: str  # "cut", "no-notes", "skip" or "review"
    cut: Optional[float] = None  # in the same units as the page lines (pixels)
    height: float = 0.0
    reason: str = ""

    @property
    def writes_file(self):
        return self.cut is not None


# --------------------------------------------------------------------------- analysis


def _bin(size):
    return round(math.log(size) / math.log(SIZE_BIN_RATIO))


def _mode_size(sizes):
    """Most common size among ``sizes`` (grouped within ~4%), as the median of that group."""
    bins = Counter(_bin(s) for s in sizes if s > 0)
    if not bins:
        return None
    winner, _ = bins.most_common(1)[0]
    return statistics.median(s for s in sizes if s > 0 and _bin(s) == winner)


def text_lines(page):
    """Lines that take part in the body/notes split, sorted top to bottom."""
    return sorted(
        (
            l
            for l in page.lines
            if l.top >= page.height * HEADER_FRACTION
            and l.width >= page.width * MIN_LINE_WIDTH_FRACTION
            and l.size > 0
        ),
        key=lambda l: l.top,
    )


def wide_lines(page):
    return [l for l in text_lines(page) if l.width >= page.width * WIDE_LINE_FRACTION]


def page_body_size(page):
    wide = wide_lines(page)
    if len(wide) < MIN_WIDE_LINES:
        return None
    return _mode_size([l.size for l in wide[:6]])


@dataclass
class BookStats:
    body: float
    notes: float
    threshold: float

    def to_json(self):
        return {"body": self.body, "notes": self.notes, "threshold": self.threshold}


def learn_book_stats(pages):
    """Body size = the most common first-lines size; notes size = the most common smaller size."""
    per_page = [s for s in (page_body_size(p) for p in pages) if s]
    body = _mode_size(per_page)
    if body is None:
        return None
    smaller = [
        l.size
        for p in pages
        for l in wide_lines(p)
        if 0.55 * body <= l.size < 0.93 * body
    ]
    notes = _mode_size(smaller) if len(smaller) >= 10 else None
    if notes is None:
        notes = 0.82 * body  # book with no (or almost no) notes
    return BookStats(body=body, notes=notes, threshold=(body + notes) / 2)


def best_split(lines, threshold):
    """Index ``k`` such that lines[:k] are body and lines[k:] are notes.

    Picks the k that disagrees with the fewest per-line size classifications, which makes
    a single mis-measured line harmless. Returns (k, misclassified_count).
    """
    small = [l.size < threshold for l in lines]
    n = len(lines)
    # cost(k) = small lines above the split + big lines below it
    cost = sum(1 for s in small if not s)  # k = 0: every big line is "below"
    best_k, best_cost = 0, cost
    for k in range(1, n + 1):
        cost += 1 if small[k - 1] else -1
        if cost <= best_cost:  # ties go to the later split (keep more of the page)
            best_k, best_cost = k, cost
    return best_k, best_cost


def looks_like_indented_block(above, below, page_width):
    """True when the first block under the cut is indented like a quote, not like footnotes."""
    body_left = statistics.median(l.left for l in above)
    block = [below[0]]
    for prev, line in zip(below, below[1:]):
        if line.top - prev.bottom > prev.size:  # a real vertical gap ends the block
            break
        block.append(line)
    return all(l.left >= body_left + QUOTE_INDENT_FRACTION * page_width for l in block)


def analyse_page(page, stats):
    lines = text_lines(page)
    wide = wide_lines(page)
    if stats is None or len(wide) < MIN_WIDE_LINES:
        return Result(page.name, "skip", height=page.height, reason="little or no text")

    k, misclassified = best_split(lines, stats.threshold)

    if k == 0:
        return Result(
            page.name, "review", height=page.height,
            reason="no body-size text found (all small type?)",
        )
    if k == len(lines):
        return Result(page.name, "no-notes", height=page.height)

    cut = (lines[k - 1].bottom + lines[k].top) / 2
    above, below = lines[:k], lines[k:]
    notes_ratio = statistics.median(l.size for l in below) / stats.body
    own_body = statistics.median(l.size for l in above)

    reasons = []
    if abs(own_body / stats.body - 1) > BODY_DEVIATION:
        reasons.append("page body size differs from the rest of the book")
    if looks_like_indented_block(above, below, page.width):
        reasons.append("indented block just below the cut (block quote?)")
    if not 0.55 <= notes_ratio <= 0.93:
        reasons.append("text below the cut is not clearly smaller than body text")
    if len(lines) - k >= 4 and statistics.median(l.width for l in lines[k:]) < page.width * NARROW_NOTES_FRACTION:
        reasons.append("text below the cut is narrow (a list or table, not footnotes?)")
    if misclassified >= max(MIXED_MIN_LINES, math.ceil(MIXED_MIN_FRACTION * len(lines))):
        reasons.append("mixed text sizes around the cut")
    status = "review" if reasons else "cut"
    return Result(page.name, status, cut=cut, height=page.height, reason="; ".join(reasons))


def analyse_book(pages):
    stats = learn_book_stats(pages)
    return stats, [analyse_page(p, stats) for p in pages]


# --------------------------------------------------------------------- line providers


def assign_line_pitch(lines, baselines):
    """Set each line's ``size`` to its line spacing: distance from its baseline to the baseline
    two lines further down, divided by two (one line down, or one up, near the end of the page).

    Measuring the ink (height of the letters) does not work: a body line with no
    descenders ("of the doctrinal section") is no taller than a footnote line that has
    some. Line spacing does not depend on which letters a line happens to contain, and
    footnotes are always set tighter than body text.

    Details that matter, all found on a real scan:
    * Look *down*, never at the smaller of the gaps above and below. Baselines jitter by a few
      pixels and a minimum turns that into a bias towards "tight". Looking down also puts the
      boundary where it belongs: the last body line has the big gap above the notes below it.
    * Average over two lines to halve the jitter.
    * Lines sharing a baseline (Tesseract sometimes splits one line in two) count as one, judged
      against the page's typical line height, not the line's own (a merged box can be oversized).
    """
    if not lines:
        return
    min_gap = 0.6 * statistics.median(l.bottom - l.top for l in lines)
    order = sorted(range(len(lines)), key=lambda i: baselines[i])

    def distinct(pos, step, count):
        """Baselines of the next ``count`` distinct lines from ``pos`` in direction ``step``."""
        found, last = [], baselines[order[pos]]
        j = pos + step
        while 0 <= j < len(order) and len(found) < count:
            b = baselines[order[j]]
            if abs(b - last) >= min_gap:
                found.append(b)
                last = b
            j += step
        return found

    for pos, i in enumerate(order):
        base = baselines[i]
        below = distinct(pos, 1, 2)
        if len(below) == 2:
            lines[i].size = (below[1] - base) / 2
        elif below:
            lines[i].size = below[0] - base
        else:
            above = distinct(pos, -1, 1)
            lines[i].size = base - above[0] if above else 0.0


def parse_tesseract_tsv(tsv, name):
    """Turn ``tesseract ... tsv`` output into a Page of Lines."""
    width = height = 0.0
    words = {}
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12:
            continue
        level, block, par, line = int(cols[0]), int(cols[2]), int(cols[3]), int(cols[4])
        left, top, w, h = (float(c) for c in cols[6:10])
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        text = cols[11].strip()
        if level == 1:
            width, height = w, h
        elif level == 5 and text and conf >= 40 and h > 3:
            words.setdefault((block, par, line), []).append((left, top, left + w, top + h, h))

    lines, baselines = [], []
    for ws in words.values():
        lines.append(
            Line(
                top=min(w[1] for w in ws),
                bottom=max(w[3] for w in ws),
                left=min(w[0] for w in ws),
                right=max(w[2] for w in ws),
                size=0.0,
            )
        )
        # Most words have no descender, so the median word bottom is the baseline.
        baselines.append(statistics.median(w[3] for w in ws))
    assign_line_pitch(lines, baselines)
    return Page(name, width, height, lines)


def ocr_image(args):
    """Run Tesseract on one image and return its raw TSV output."""
    path, lang = args
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    return subprocess.run(
        ["tesseract", path, "stdout", "-l", lang, "--psm", "3", "tsv"],
        capture_output=True, text=True, env=env, check=True,
    ).stdout


def parse_pdf_bbox(html, scale, names):
    """Parse ``pdftotext -bbox-layout`` output (points) into Pages scaled to pixels."""
    pages = []
    for i, chunk in enumerate(re.split(r"<page ", html)[1:]):
        w = float(re.search(r'width="([\d.]+)"', chunk).group(1)) * scale
        h = float(re.search(r'height="([\d.]+)"', chunk).group(1)) * scale
        lines = []
        for m in re.finditer(
            r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">', chunk
        ):
            x0, y0, x1, y1 = (float(v) * scale for v in m.groups())
            lines.append(Line(top=y0, bottom=y1, left=x0, right=x1, size=y1 - y0))
        pages.append(Page(names[i] if i < len(names) else str(i + 1), w, h, lines))
    return pages


# ----------------------------------------------------------------------- files / CLI


def find_source_folder(base):
    for folder in SOURCE_FOLDERS:
        path = os.path.join(base, folder)
        if os.path.isdir(path):
            return path
    return None


def page_files(folder, file_type):
    """(stem, path) for every page image, in the order the WPF app shows them:
    ``_N`` files first (numeric), then ``N`` files (numeric)."""
    ext = "." + file_type.lower().lstrip(".")
    underscore, plain = [], []
    for name in os.listdir(folder):
        stem, e = os.path.splitext(name)
        if e.lower() != ext:
            continue
        digits = stem[1:] if stem.startswith("_") else stem
        if not digits.isdigit():
            continue
        (underscore if stem.startswith("_") else plain).append((int(digits), stem, name))
    ordered = sorted(underscore) + sorted(plain)
    return [(stem, os.path.join(folder, name)) for _, stem, name in ordered]


def render_pdf(pdf, folder, dpi, jobs):
    os.makedirs(folder, exist_ok=True)
    info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True, check=True).stdout
    count = int(re.search(r"Pages:\s+(\d+)", info).group(1))
    todo = [(pdf, folder, n, dpi) for n in range(1, count + 1)]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(_render_one, todo))
    return count


def _render_one(args):
    pdf, folder, n, dpi = args
    target = os.path.join(folder, str(n))
    if os.path.exists(target + ".png"):
        return
    subprocess.run(
        ["pdftoppm", "-f", str(n), "-l", str(n), "-r", str(dpi), "-png", "-singlefile", pdf, target],
        check=True,
    )


def load_pages_from_images(files, lang, jobs, cache_dir, use_cache):
    """OCR every page (raw Tesseract output is cached next to the book) and parse the lines."""
    tsvs = [None] * len(files)
    todo = []
    for i, (stem, path) in enumerate(files):
        cached = os.path.join(cache_dir, stem + ".tsv")
        if use_cache and os.path.exists(cached) and os.path.getmtime(cached) >= os.path.getmtime(path):
            with open(cached, encoding="utf-8") as fh:
                tsvs[i] = fh.read()
        else:
            todo.append(i)
    if todo:
        print(f"OCR: {len(todo)} page(s) with {jobs} worker(s)...", file=sys.stderr)
        os.makedirs(cache_dir, exist_ok=True)
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            for i, tsv in zip(todo, pool.map(ocr_image, [(files[i][1], lang) for i in todo], chunksize=4)):
                tsvs[i] = tsv
                with open(os.path.join(cache_dir, files[i][0] + ".tsv"), "w", encoding="utf-8") as fh:
                    fh.write(tsv)
    return [parse_tesseract_tsv(tsv, stem) for tsv, (stem, _) in zip(tsvs, files)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", help="book folder (the BasePath of FootnoteCrop)")
    ap.add_argument("--file-type", default="png", help="page image extension (default png)")
    ap.add_argument("--pdf", help="start from this PDF: render its pages into BASE/Straight first")
    ap.add_argument("--dpi", type=int, default=150, help="render resolution for --pdf (default 150)")
    ap.add_argument("--use-text-layer", action="store_true",
                    help="with --pdf: use the PDF's own text positions instead of running OCR")
    ap.add_argument("--lang", default="eng", help="Tesseract language(s), e.g. eng+lat")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1, help="parallel workers")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace coordinate files that already exist (default: keep your manual work)")
    ap.add_argument("--no-cache", action="store_true", help="ignore saved OCR results")
    ap.add_argument("--dry-run", action="store_true", help="analyse and report, write no coordinate files")
    args = ap.parse_args(argv)

    base = args.base
    if args.pdf:
        folder = os.path.join(base, "Straight")
        print(f"Rendering {args.pdf} at {args.dpi} dpi into {folder} ...", file=sys.stderr)
        render_pdf(args.pdf, folder, args.dpi, args.jobs)
        args.file_type = "png"
    else:
        folder = find_source_folder(base)
        if folder is None:
            ap.error(f"none of {', '.join(SOURCE_FOLDERS)} found under {base}")

    files = page_files(folder, args.file_type)
    if not files:
        ap.error(f"no .{args.file_type} page images in {folder}")

    if args.pdf and args.use_text_layer:
        html = subprocess.run(
            ["pdftotext", "-bbox-layout", args.pdf, "-"], capture_output=True, text=True, check=True
        ).stdout
        pages = parse_pdf_bbox(html, args.dpi / 72.0, [s for s, _ in files])
        if len(pages) != len(files):
            ap.error("PDF page count does not match the rendered images")
    else:
        pages = load_pages_from_images(
            files, args.lang, args.jobs, os.path.join(base, "AutoCutData"), not args.no_cache
        )

    stats, results = analyse_book(pages)
    if stats is None:
        print("Could not find enough body text to learn the book's layout.", file=sys.stderr)
        return 1

    coord_dir = os.path.join(base, "CoordinateData")
    written = kept = 0
    if not args.dry_run:
        os.makedirs(coord_dir, exist_ok=True)
    for r in results:
        if not r.writes_file or args.dry_run:
            continue
        target = os.path.join(coord_dir, r.name + ".txt")
        if os.path.exists(target) and not args.overwrite:
            kept += 1
            continue
        with open(target, "w") as fh:
            fh.write(str(int(round(r.cut))))
        written += 1

    report = os.path.join(base, "autocut-report.csv")
    with open(report, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["page", "status", "cut_y", "cut_percent", "reason"])
        for r in results:
            w.writerow([
                r.name, r.status,
                "" if r.cut is None else int(round(r.cut)),
                "" if r.cut is None or not r.height else f"{100 * r.cut / r.height:.0f}",
                r.reason,
            ])

    counts = Counter(r.status for r in results)
    print(f"Body text ~{stats.body:.1f}px, footnotes ~{stats.notes:.1f}px (split at {stats.threshold:.1f}px)")
    print(f"{len(results)} pages: {counts['cut']} cut, {counts['review']} to review, "
          f"{counts['no-notes']} without notes, {counts['skip']} skipped (blank/title/image)")
    if not args.dry_run:
        print(f"Wrote {written} coordinate file(s) to {coord_dir}"
              + (f" (kept {kept} existing; use --overwrite to replace)" if kept else ""))
    flagged = [r.name for r in results if r.status == "review"]
    if flagged:
        print("Look at these pages: " + ", ".join(flagged))
    print(f"Full report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
