from evaluation import conclusion_accuracy, retrieval_metrics


def test_retrieval_metrics():
    cases = [{"relevant_source_types": ["study"]}, {"relevant_source_types": ["guideline"]}]
    results = [[{"source_type": "study"}], [{"source_type": "news"}]]
    assert retrieval_metrics(cases, results) == {
        "recall_at_k": 0.5,
        "mean_reciprocal_rank": 0.5,
    }


def test_conclusion_accuracy():
    assert conclusion_accuracy(["supported", "mixed"], ["supported", "insufficient"]) == 0.5
