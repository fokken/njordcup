"""Small labeled smoke evaluation. Fixtures are analyzed as text, never executed."""
import argparse
import json
from pathlib import Path

from .agent import review
from .provider import OpenAIProvider


def evaluate(cases, provider):
    rows = []
    true_positive = false_positive = false_negative = 0
    for case in cases:
        report = review({"app.py": case["source"]}, ["app.py"], [], provider)
        expected = set(case["expected_cwes"])
        observed = {f["cwe"] for f in report["findings"]}
        true_positive += len(expected & observed)
        false_positive += len(observed - expected)
        false_negative += len(expected - observed)
        rows.append({"case": case["id"], "expected": sorted(expected), "observed": sorted(observed),
                     "status": report["status"], "errors": report["errors"]})
    return {"cases": rows, "true_positive": true_positive, "false_positive": false_positive, "false_negative": false_negative,
            "precision": true_positive / (true_positive + false_positive) if true_positive + false_positive else None,
            "recall": true_positive / (true_positive + false_negative) if true_positive + false_negative else None,
            "complete": all(r["status"] == "complete" for r in rows),
            "usage": {k: getattr(provider, k, 0) for k in ("calls", "input_tokens", "output_tokens")},
            "note": "CWE presence per fixture, not finding-level precision. Four smoke fixtures do not establish production accuracy."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evals/cases.json"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--api-key-env", default="OLLAMA_API_KEY")
    parser.add_argument("--max-calls", type=int, default=30)
    args = parser.parse_args()
    provider = OpenAIProvider(args.model, args.max_calls, base_url=args.base_url, api_key_env=args.api_key_env)
    result = evaluate(json.loads(args.cases.read_text()), provider)
    print(json.dumps(result, indent=2))
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
