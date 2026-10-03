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

    HEADER = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
              "1\t1\t0\t0\t0\t0\t0\t0\t1000\t1500\t-1\t"]

    def tsv_lines(self, specs):
        """specs: list of (baseline, ink_height, [word_heights]) -> TSV with one row per word."""
        rows = list(self.HEADER)
        for n, (baseline, _, heights) in enumerate(specs, start=1):
            for i, h in enumerate(heights):
                rows.append(f"5\t1\t1\t1\t{n}\t{i + 1}\t{100 + i * 90}\t{baseline - h}\t80\t{h}\t95\tw")
        return "\n".join(rows)

    def test_size_is_line_spacing_not_ink_height(self):
        # Body (27px apart) and notes (20px apart). The last body line has no descenders, so its
        # ink is *shorter* than a note line's - spacing still tells them apart.
        body = [(300 + 27 * i, 0, [16, 17, 16, 17]) for i in range(5)]      # ink ~16-17px
        notes = [(500 + 20 * i, 0, [18, 20, 19, 21]) for i in range(5)]     # ink ~18-21px
        page = ac.parse_tesseract_tsv(self.tsv_lines(body + notes), "1")
        sizes = [round(l.size) for l in sorted(page.lines, key=lambda l: l.top)]
        # spacing 2 lines down: body 27; the last body lines see the big gap to the notes
        self.assertEqual(sizes, [27, 27, 27, 60, 56, 20, 20, 20, 20, 20])

    def test_gap_before_the_notes_does_not_change_the_boundary_sizes(self):
        specs = [(300 + 27 * i, 0, [17, 17]) for i in range(3)] + [(480 + 20 * i, 0, [17, 17]) for i in range(3)]
        page = ac.parse_tesseract_tsv(self.tsv_lines(specs), "1")
        sizes = [round(l.size) for l in sorted(page.lines, key=lambda l: l.top)]
        self.assertEqual(sizes, [27, 76, 73, 20, 20, 20])

    def test_one_oversized_box_does_not_hide_its_neighbour(self):
        # first note line box merged with something above it (34px tall); the next note line's
        # baseline is only 15px below the merged box's median-bottom baseline
        rows = list(self.HEADER)
        specs = [(300, 18), (327, 18), (354, 18), (560, 34), (575, 18), (595, 18), (615, 18)]
        for n, (baseline, height) in enumerate(specs, start=1):
            rows.append(f"5\t1\t1\t1\t{n}\t1\t100\t{baseline - height}\t600\t{height}\t95\tw")
        page = ac.parse_tesseract_tsv("\n".join(rows), "1")
        by_top = sorted(page.lines, key=lambda l: l.top)
        self.assertAlmostEqual(by_top[3].size, 17.5)  # the oversized box still sees its true neighbours

    def test_a_line_split_in_two_is_not_its_own_neighbour(self):
        rows = list(self.HEADER)
        for n, baseline in enumerate([300, 327, 354], start=1):
            rows.append(f"5\t1\t1\t1\t{n}\t1\t100\t{baseline - 18}\t300\t18\t95\tw")
        rows.append("5\t1\t2\t1\t1\t1\t700\t309\t30\t18\t95\tw")  # fragment on the middle line's baseline
        page = ac.parse_tesseract_tsv("\n".join(rows), "1")
        self.assertEqual({round(l.size) for l in page.lines}, {27})  # the fragment adds no tiny gap

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
