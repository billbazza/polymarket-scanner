import importlib
import os
import unittest


class PerplexityValidationTests(unittest.TestCase):
    def setUp(self):
        self.original_key = os.environ.pop("PERPLEXITY_API_KEY", None)
        import perplexity
        self.perplexity = importlib.reload(perplexity)

    def tearDown(self):
        if self.original_key is None:
            os.environ.pop("PERPLEXITY_API_KEY", None)
        else:
            os.environ["PERPLEXITY_API_KEY"] = self.original_key
        import perplexity
        importlib.reload(perplexity)

    def test_evaluate_signal_disabled_without_key(self):
        result = self.perplexity.evaluate_signal({"event": "Test signal"})
        self.assertEqual(result["status"], "disabled")
        self.assertFalse(result["profitable_candidate"])
        self.assertEqual(result["confidence"], 0.0)

    def test_annotate_profitable_candidate_adds_metadata(self):
        opp = {"event": "Stage 2 Test"}
        self.perplexity.annotate_profitable_candidate(opp)
        self.assertIn("perplexity", opp)
        self.assertFalse(opp.get("profitable_candidate_feature"))
        self.assertEqual(opp.get("perplexity_status"), "disabled")
        self.assertIn("profitable_candidate_reason", opp)


if __name__ == "__main__":
    unittest.main()
