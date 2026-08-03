import unittest

from fugu_local.answers import (
    cluster_answers,
    extract_final_answer,
    majority_vote,
    normalize_answer,
)


class NormalizeAnswerTests(unittest.TestCase):
    def test_equivalent_forms_normalize_identically(self):
        expected = normalize_answer("42")
        equivalents = [
            "42",
            "42.0",
            "**42**",
            "The answer is 42.",
            "The final answer is 42.",
            "Answer: 42",
            "答え: ４２",
        ]
        for value in equivalents:
            with self.subTest(value=value):
                self.assertEqual(normalize_answer(value), expected)

    def test_japanese_full_width_and_punctuation_variants_match(self):
        self.assertEqual(normalize_answer("東京。"), normalize_answer("東京"))
        self.assertEqual(normalize_answer("東京"), normalize_answer("東京"))
        self.assertEqual(normalize_answer("最終回答: 東京"), normalize_answer("東京"))

    def test_different_answers_stay_distinct(self):
        self.assertNotEqual(normalize_answer("42"), normalize_answer("43"))
        self.assertNotEqual(normalize_answer("Tokyo"), normalize_answer("Paris"))

    def test_numeric_normalization_does_not_touch_non_numeric_strings(self):
        # A version-number-like string must not be treated as a bare number.
        self.assertEqual(normalize_answer("v1.0"), "v1.0")

    def test_prefix_stripping_is_limited_to_the_documented_list(self):
        # "I think the answer is 42" is NOT covered by ANSWER_PREFIXES because
        # the prefix must anchor the start of the (trimmed) text.
        self.assertNotEqual(
            normalize_answer("I think the answer is 42"),
            normalize_answer("42"),
        )


class ClusterAnswersTests(unittest.TestCase):
    def test_equivalent_answers_form_one_cluster(self):
        contents = ["42", "**42**", "The answer is 42.", "43"]
        clusters = cluster_answers(contents)
        self.assertEqual(len(clusters), 2)
        cluster_sizes = sorted(len(cluster) for cluster in clusters)
        self.assertEqual(cluster_sizes, [1, 3])

    def test_is_deterministic(self):
        contents = ["Tokyo", "tokyo", "Paris", "Tokyo", "London"]
        first = cluster_answers(contents)
        second = cluster_answers(contents)
        self.assertEqual(first, second)

    def test_cluster_indices_are_ascending_and_ordered_by_first_appearance(self):
        contents = ["b", "a", "b", "a", "c"]
        clusters = cluster_answers(contents)
        self.assertEqual(clusters, [[0, 2], [1, 3], [4]])


class MajorityVoteTests(unittest.TestCase):
    def test_majority_wins(self):
        winner, votes, cluster_count = majority_vote(["42", "42", "43"])
        self.assertEqual(winner, "42")
        self.assertEqual(votes, 2)
        self.assertEqual(cluster_count, 2)

    def test_tie_breaks_to_first_appearance(self):
        winner, votes, cluster_count = majority_vote(["43", "42"])
        self.assertEqual(winner, "43")
        self.assertEqual(votes, 1)
        self.assertEqual(cluster_count, 2)

    def test_empty_input_returns_empty_winner(self):
        self.assertEqual(majority_vote([]), ("", 0, 0))


class ExtractFinalAnswerTests(unittest.TestCase):
    def test_returns_last_prefixed_line(self):
        text = "Step 1: think.\nStep 2: check.\nFinal answer: 42\n"
        self.assertEqual(extract_final_answer(text), "Final answer: 42")

    def test_falls_back_to_last_non_empty_line(self):
        text = "line one\nline two\n\n"
        self.assertEqual(extract_final_answer(text), "line two")

    def test_empty_text_returns_empty_string(self):
        self.assertEqual(extract_final_answer(""), "")
        self.assertEqual(extract_final_answer("\n\n"), "")


if __name__ == "__main__":
    unittest.main()
