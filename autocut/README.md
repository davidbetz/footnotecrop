# autocut — propose the footnote crop lines automatically

`autocut.py` does the "snip, snip, snip" for you. It looks at every page, decides where the
footnotes start, and writes the same `CoordinateData/<page>.txt` files the FootnoteCrop app
writes. You then open FootnoteCrop and only touch the pages that are wrong.

It is plain Python — no AI model, no network, no API key. The same book gives the same
answer every time.

## Install

* Python 3.8+ (standard library only)
* [Tesseract](https://github.com/tesseract-ocr/tesseract) on your `PATH` (for page images).
  Windows installer: <https://github.com/UB-Mannheim/tesseract/wiki>
* Optional, for `--pdf`: `pdftoppm`, `pdfinfo`, `pdftotext` from
  [poppler](https://poppler.freedesktop.org/) (on Windows: `choco install poppler`).

## Use it

From your existing folders (same layout and lookup order as FootnoteCrop —
`VerticalCropped`, `TopCropped`, `Straight`, `Cropped`):

    python autocut.py C:\_BOOK\ScannedBook --file-type png

From a PDF (renders the pages into `<book>/Straight/1.png, 2.png, …` first):

    python autocut.py C:\_BOOK\ScannedBook --pdf book.pdf --dpi 150

If the PDF already has a good text layer, add `--use-text-layer` to skip OCR (seconds
instead of minutes).

When it finishes you get:

* `CoordinateData/<page>.txt` — one per page that has footnotes, exactly the format the
  app and the ImageMagick script already use. **Existing files are never overwritten**
  (so your manual work is safe) unless you pass `--overwrite`.
* `autocut-report.csv` — every page, its status, cut position and, for flagged pages, why.
* `AutoCutData/` — cached OCR results, so re-running is quick.

Pages with no footnotes get no file, so your post-processing script copies them untouched.

## How it decides

1. OCR gives the position and height of every text line on every page.
2. From all pages together it learns the book's body-text size and footnote size.
3. On each page it finds the one split that best separates body-size lines (above) from
   smaller lines (below), and puts the crop in the gap under the last body line. It does
   not matter how tall the footnote block is, or whether the page is mostly notes.
4. A single badly-measured line can't move the cut; the split with the fewest
   disagreements wins.

## Pages it flags for a human look

These get a proposed cut (when there is one) *and* a line in the report, so review them
before cropping:

* all the text on the page is small (index, bibliography, tables, notes-only pages),
* text below the cut is narrow like a list or table, not footnote paragraphs,
* the first block under the cut is indented like a block quote,
* the page's body size differs from the rest of the book, or sizes are mixed around the cut.

Blank, title and image pages (too little text) are skipped.

## Limits

* It judges by type size and layout. A book whose notes are the same size as the body
  text can't be handled this way.
* A small block quote at the very end of the body text looks like notes; it is flagged
  when indented, but check those.
* Footnote markers inside the body text (superscript numbers) stay in the image; this tool
  only removes the note block at the bottom.

## Tests

    python -m unittest

Run from this folder. The tests use synthetic pages and need neither Tesseract nor a book.
