import contextlib
import io
import os
import tempfile
import unittest

import autocut as ac

W, H = 1000.0, 1500.0
BODY, NOTE = 20.0, 16.0


def make_page(name, body_lines, note_lines, quote_lines=0, header=True, note_width=800.0, last_note_width=None):
    """A page: running head, body lines, optional small block quote, then notes at the bottom."""
    lines = []
    if header:
        lines.append(ac.Line(50, 70, 400, 600, BODY))
    y = 200.0
    for _ in range(body_lines):
        lines.append(ac.Line(y, y + BODY, 100, 900, BODY))
        y += 30
    for _ in range(quote_lines):
        lines.append(ac.Line(y, y + NOTE, 160, 840, NOTE))
        y += 26
    y += 40  # gap above the notes
    first_note_top = y
    for i in range(note_lines):
        w = note_width if (i < note_lines - 1 or last_note_width is None) else last_note_width
        lines.append(ac.Line(y, y + NOTE, 100, 100 + w, NOTE))
        y += 22
    return ac.Page(name, W, H, lines), first_note_top


def page_to_tsv(page):
    """Tesseract-style TSV with one word per line, spanning the whole line."""
    rows = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            f"1\t1\t0\t0\t0\t0\t0\t0\t{page.width:.0f}\t{page.height:.0f}\t-1\t"]
    for i, l in enumerate(page.lines):
        rows.append(f"5\t1\t1\t1\t{i + 1}\t1\t{l.left:.0f}\t{l.top:.0f}\t{l.width:.0f}\t{l.size:.0f}\t95\tword")
    return "\n".join(rows)


def book(n=30):
    return [make_page(str(i), 20, 3 + i % 4)[0] for i in range(1, n + 1)]


class StatsTests(unittest.TestCase):
    def test_learns_body_and_note_sizes(self):
        stats = ac.learn_book_stats(book())
        self.assertAlmostEqual(stats.body, BODY, delta=0.5)
        self.assertAlmostEqual(stats.notes, NOTE, delta=0.5)
        self.assertAlmostEqual(stats.threshold, 18.0, delta=0.5)

    def test_book_without_notes_falls_back(self):
        pages = [make_page(str(i), 20, 0)[0] for i in range(10)]
        stats = ac.learn_book_stats(pages)
        self.assertLess(stats.notes, stats.body)


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.stats = ac.learn_book_stats(book())

    def test_cut_is_in_the_gap_above_the_notes(self):
        page, notes_top = make_page("1", 12, 4)
        r = ac.analyse_page(page, self.stats)
        last_body_bottom = 200 + 11 * 30 + BODY
        self.assertEqual(r.status, "cut")
        self.assertGreater(r.cut, last_body_bottom)
        self.assertLess(r.cut, notes_top)

    def test_mostly_notes_page(self):
        page, notes_top = make_page("1", 2, 25)
        r = ac.analyse_page(page, self.stats)
        self.assertEqual(r.status, "cut")
        self.assertLess(r.cut, notes_top)
        self.assertLess(r.cut, 0.2 * H)

    def test_page_without_notes_has_no_cut(self):
        page, _ = make_page("1", 20, 0)
        r = ac.analyse_page(page, self.stats)
        self.assertEqual(r.status, "no-notes")
        self.assertFalse(r.writes_file)

    def test_blank_page_is_skipped(self):
        r = ac.analyse_page(ac.Page("1", W, H, [ac.Line(50, 70, 400, 600, BODY)]), self.stats)
        self.assertEqual(r.status, "skip")

    def test_short_last_body_line_is_not_cut_off(self):
        page, notes_top = make_page("1", 10, 3)
        # the last body line is the short end of a paragraph, only 6% of the page wide
        page.lines = [l for l in page.lines if l.top != 200 + 9 * 30]
        page.lines.append(ac.Line(200 + 9 * 30, 200 + 9 * 30 + BODY, 100, 160, BODY))
        r = ac.analyse_page(page, self.stats)
        self.assertGreater(r.cut, 200 + 9 * 30 + BODY)

    def test_block_quote_at_the_end_of_the_body_stays_with_the_body_or_is_flagged(self):
        page, notes_top = make_page("1", 10, 3, quote_lines=3)
        r = ac.analyse_page(page, self.stats)
        quote_top = 200 + 10 * 30
        # either the cut is below the quote, or the page is sent to review - never silently inside it
        if r.cut is not None and r.cut > quote_top:
            self.assertLess(r.cut, notes_top)
        else:
            self.assertEqual(r.status, "review")

    def test_one_mismeasured_note_line_does_not_move_the_cut(self):
        page, notes_top = make_page("1", 12, 6)
        page.lines[-3].size = BODY  # OCR noise: one note line measured at body size
        r = ac.analyse_page(page, self.stats)
        self.assertLess(r.cut, notes_top)

    def test_page_of_only_small_type_is_flagged_not_cut(self):
        lines = [ac.Line(200 + i * 22, 216 + i * 22, 100, 900, NOTE) for i in range(30)]
        r = ac.analyse_page(ac.Page("1", W, H, lines), self.stats)
        self.assertEqual(r.status, "review")
        self.assertFalse(r.writes_file)

    def test_narrow_list_below_the_cut_is_flagged(self):
        page, _ = make_page("1", 8, 12, note_width=150.0)
        r = ac.analyse_page(page, self.stats)
        self.assertEqual(r.status, "review")
        self.assertIn("narrow", r.reason)

    def test_single_short_footnote_is_not_flagged(self):
        page, _ = make_page("1", 20, 1, note_width=200.0)
        r = ac.analyse_page(page, self.stats)
        self.assertEqual(r.status, "cut")

    def test_odd_page_body_size_is_flagged(self):
        lines = [ac.Line(200 + i * 40, 228 + i * 40, 100, 900, 28.0) for i in range(8)]
        lines += [ac.Line(700 + i * 22, 716 + i * 22, 100, 900, NOTE) for i in range(3)]
        r = ac.analyse_page(ac.Page("1", W, H, lines), self.stats)
        self.assertEqual(r.status, "review")


class ParseTests(unittest.TestCase):
    TSV = "\n".join(
        [
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "1\t1\t0\t0\t0\t0\t0\t0\t1000\t1500\t-1\t",
            "5\t1\t1\t1\t1\t1\t100\t300\t80\t20\t95\tHello",
            "5\t1\t1\t1\t1\t2\t200\t302\t90\t24\t96\tworld",
            "5\t1\t1\t1\t1\t3\t300\t300\t10\t20\t10\tjunk",  # low confidence: ignored
            "5\t1\t1\t1\t2\t1\t100\t340\t120\t20\t90\tSecond",
            "5\t1\t1\t1\t2\t2\t230\t340\t5\t2\t90\tspeck",  # tiny: ignored
        ]
    )

    def test_tesseract_tsv(self):
        page = ac.parse_tesseract_tsv(self.TSV, "7")
        self.assertEqual((page.width, page.height), (1000, 1500))
        self.assertEqual(len(page.lines), 2)
        first = page.lines[0]
        self.assertEqual((first.left, first.right, first.top, first.bottom), (100, 290, 300, 326))

    def test_line_size_is_the_line_span_not_the_word_heights(self):
        # Real OCR: a line of short words has short word boxes, yet the line is full size.
        # Words: some with ascenders only, some with descenders only, some x-height only.
        rows = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
                "1\t1\t0\t0\t0\t0\t0\t0\t1000\t1500\t-1\t"]
        spans = [(300, 316), (296, 316), (300, 321), (296, 321), (300, 313)]  # (top, bottom)
        for i, (top, bottom) in enumerate(spans):
            rows.append(f"5\t1\t1\t1\t1\t{i + 1}\t{100 + i * 90}\t{top}\t80\t{bottom - top}\t95\tw")
        # a second line made only of x-height words
        for i in range(4):
            rows.append(f"5\t1\t1\t1\t2\t{i + 1}\t{100 + i * 90}\t340\t80\t12\t95\tw")
        page = ac.parse_tesseract_tsv("\n".join(rows), "1")
        full, x_only = page.lines
        self.assertEqual(full.size, 25)  # 296 .. 321

    def test_outlier_tall_word_does_not_inflate_the_size(self):
        rows = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
                "1\t1\t0\t0\t0\t0\t0\t0\t1000\t1500\t-1\t"]
        for i in range(5):
            rows.append(f"5\t1\t1\t1\t1\t{i + 1}\t{100 + i * 90}\t300\t80\t18\t95\tw")
        rows.append("5\t1\t1\t1\t1\t6\t600\t280\t30\t60\t95\tx")  # merged-in tall blob
        (line,) = ac.parse_tesseract_tsv("\n".join(rows), "1").lines
        self.assertEqual(line.size, 18)
        self.assertEqual(line.top, 280)  # geometry still covers the blob

    def test_pdf_bbox_is_scaled_to_pixels(self):
        html = (
            '<page width="500.0" height="800.0">'
            '<line xMin="10.0" yMin="20.0" xMax="110.0" yMax="32.0"><word>a</word></line></page>'
        )
        (page,) = ac.parse_pdf_bbox(html, 2.0, ["1"])
        self.assertEqual((page.width, page.height), (1000, 1600))
        self.assertEqual((page.lines[0].top, page.lines[0].bottom, page.lines[0].size), (40, 64, 24))


class FileTests(unittest.TestCase):
    def test_source_folder_order_matches_the_wpf_app(self):
        with tempfile.TemporaryDirectory() as base:
            for name in ("Cropped", "Straight"):
                os.mkdir(os.path.join(base, name))
            self.assertTrue(ac.find_source_folder(base).endswith("Straight"))
            os.mkdir(os.path.join(base, "VerticalCropped"))
            self.assertTrue(ac.find_source_folder(base).endswith("VerticalCropped"))

    def test_page_order_matches_the_wpf_app(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("10.png", "2.png", "_3.png", "_11.png", "notes.png", "5.jpg"):
                open(os.path.join(folder, name), "w").close()
            stems = [stem for stem, _ in ac.page_files(folder, "png")]
            self.assertEqual(stems, ["_3", "_11", "2", "10"])

    def test_cli_writes_integer_coordinate_files_and_keeps_existing(self):
        with tempfile.TemporaryDirectory() as base:
            os.mkdir(os.path.join(base, "Straight"))
            pages = book(12)
            stats, results = ac.analyse_book(pages)
            self.assertTrue(all(r.writes_file for r in results))
            os.mkdir(os.path.join(base, "CoordinateData"))
            keep = os.path.join(base, "CoordinateData", "1.txt")
            with open(keep, "w") as fh:
                fh.write("123")
            # feed main() cached OCR output (the cache is only trusted when newer than the image)
            os.mkdir(os.path.join(base, "AutoCutData"))
            for p in pages:
                image = os.path.join(base, "Straight", p.name + ".png")
                open(image, "w").close()
                cache = os.path.join(base, "AutoCutData", p.name + ".tsv")
                with open(cache, "w") as fh:
                    fh.write(page_to_tsv(p))
                future = os.path.getmtime(image) + 10
                os.utime(cache, (future, future))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ac.main([base]), 0)
            with open(keep) as fh:
                self.assertEqual(fh.read(), "123")  # not overwritten
            with open(os.path.join(base, "CoordinateData", "2.txt")) as fh:
                self.assertRegex(fh.read(), r"^\d+$")
            self.assertTrue(os.path.exists(os.path.join(base, "autocut-report.csv")))


if __name__ == "__main__":
    unittest.main()
