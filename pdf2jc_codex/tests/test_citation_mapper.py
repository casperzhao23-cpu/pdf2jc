import unittest

from pdf2jc.citation_mapper import normalize_citation


class CitationNormalizationTests(unittest.TestCase):
    def test_single_panel(self):
        self.assertEqual(normalize_citation("Fig. 1A"), ["Fig1A"])

    def test_en_dash_range(self):
        self.assertEqual(
            normalize_citation("Fig. 1A–C"),
            ["Fig1A", "Fig1B", "Fig1C"],
        )

    def test_hyphen_range(self):
        self.assertEqual(
            normalize_citation("Fig. 1A-C"),
            ["Fig1A", "Fig1B", "Fig1C"],
        )

    def test_comma_separated_panels(self):
        self.assertEqual(normalize_citation("Fig. 1A, B"), ["Fig1A", "Fig1B"])

    def test_and_separated_panels(self):
        self.assertEqual(normalize_citation("Fig. 1A and B"), ["Fig1A", "Fig1B"])

    def test_compact_comma_separated_panels(self):
        self.assertEqual(normalize_citation("Fig. 1A,B"), ["Fig1A", "Fig1B"])

    def test_figure_word_range(self):
        self.assertEqual(
            normalize_citation("Figure 2D–F"),
            ["Fig2D", "Fig2E", "Fig2F"],
        )

    def test_figure_word_and_separated_panels(self):
        self.assertEqual(normalize_citation("Figure 2D and E"), ["Fig2D", "Fig2E"])

    def test_multiple_figures(self):
        self.assertEqual(normalize_citation("Figs. 2A and 3B"), ["Fig2A", "Fig3B"])

    def test_figure_number_then_comma_panels(self):
        self.assertEqual(
            normalize_citation("Fig. 1, D and E"),
            ["Fig1D", "Fig1E"],
        )

    def test_extended_data_figure(self):
        self.assertEqual(normalize_citation("Extended Data Fig. 3A"), ["Fig3A"])

    def test_supplementary_figure(self):
        self.assertEqual(normalize_citation("Supplementary Fig. 5B"), ["Fig5B"])


if __name__ == "__main__":
    unittest.main()
