"""Взвешенные показатели распределения без БД и развертывания частот в выборку."""

import unittest

from dashboard.presentation import distribution_stats, score_text


def frequencies(*values):
    return [{"primary_score": score, "participants": count} for score, count in values]


class DistributionStatisticsTests(unittest.TestCase):
    def test_mean_and_odd_median_use_participant_frequencies(self):
        self.assertEqual(distribution_stats(frequencies((0, 1), (10, 4))), {
            "participants": 5, "mean": 8, "median": 10, "modes": [10],
        })

    def test_even_median_averages_two_central_participants_across_a_gap(self):
        self.assertEqual(distribution_stats(frequencies((1, 2), (7, 1), (9, 1))), {
            "participants": 4, "mean": 4.5, "median": 4, "modes": [1],
        })

    def test_unsorted_duplicate_scores_are_combined_before_finding_modes(self):
        result = distribution_stats(frequencies((4, 2), (0, 3), (9, 1), (4, 2)))
        self.assertEqual(result, {
            "participants": 8, "mean": 3.125, "median": 4, "modes": [4],
        })

    def test_multiple_modes_are_preserved_in_score_order(self):
        self.assertEqual(distribution_stats(frequencies((8, 3), (5, 1), (2, 3))), {
            "participants": 7, "mean": 5, "median": 5, "modes": [2, 8],
        })

    def test_uniform_frequencies_have_no_distinguished_mode(self):
        self.assertEqual(distribution_stats(frequencies((0, 2), (10, 2)))["modes"], [])

    def test_one_zero_score_is_a_valid_result(self):
        self.assertEqual(distribution_stats(frequencies((0, 1))), {
            "participants": 1, "mean": 0, "median": 0, "modes": [0],
        })

    def test_empty_and_zero_frequencies_do_not_create_zero_statistics(self):
        for rows in ([], frequencies((10, 0))):
            with self.subTest(rows=rows):
                self.assertEqual(distribution_stats(rows), {
                    "participants": 0, "mean": None, "median": None, "modes": [],
                })

    def test_large_frequencies_do_not_require_a_list_of_participants(self):
        self.assertEqual(distribution_stats(frequencies((0, 10**12), (10, 10**12))), {
            "participants": 2 * 10**12, "mean": 5, "median": 5, "modes": [],
        })

    def test_negative_frequency_is_rejected(self):
        with self.assertRaises(ValueError):
            distribution_stats(frequencies((0, -1), (10, 2)))

    def test_score_text_preserves_zero_and_uses_a_decimal_comma(self):
        self.assertEqual(score_text(0), "0")
        self.assertEqual(score_text(12), "12")
        self.assertEqual(score_text(12.345), "12,35")
        self.assertEqual(score_text(None), "—")


if __name__ == "__main__":
    unittest.main()
