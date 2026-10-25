"""
eval_accuracy.py — run a fixed set of questions through the agent and score
each answer against known-correct ("ground truth") values computed directly
from the database.

Run:  ./.venv/bin/python eval_accuracy.py
Tests against Chinook.db (the default DB_PATH in agent.py). This makes real
API calls, so it needs ANTHROPIC_API_KEY in .env (10 questions, a few cents).

The ground-truth values are themselves checked against Chinook.db by
tests/test_ground_truth.py, so a typo in an expected answer fails offline.
"""

import re
import sys

import anthropic
import agent  # reuse the real agent code — same prompt, same loop

# 10 questions, easy -> hard. Each expected item must appear in the answer:
# numbers are matched as numbers (so "3,503" and "3503" both count, and so do
# "2328.6" and "2,328.60"), words are matched case-insensitively.
TESTS = [
    ("How many tracks are in the database?",                       ["3503"]),
    ("How many customers are there?",                              ["59"]),
    ("How many customers are from the USA?",                       ["13"]),
    ("How many tracks are longer than 5 minutes?",                 ["1069"]),
    ("Which genre has the most tracks, and how many?",             ["Rock", "1297"]),
    ("What is the total revenue across all invoices?",             ["2328.60"]),
    ("Which billing country has the highest total revenue?",       ["USA", "523.06"]),
    ("How many albums does the artist AC/DC have?",                ["2"]),
    ("What is the total revenue from the Rock genre?",             ["826.65"]),
    ("Which artist generated the most revenue, and how much?",     ["Iron Maiden", "138.60"]),
]

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def answer_matches(answer: str, expected: list) -> bool:
    """True if every expected word or number shows up in the answer."""
    numbers = [float(n) for n in _NUMBER.findall(answer.replace(",", ""))]
    for item in expected:
        if _NUMBER.fullmatch(item):
            if not any(abs(n - float(item)) < 0.005 for n in numbers):
                return False
        elif item.lower() not in answer.lower():
            return False
    return True


def main():
    client = anthropic.Anthropic()
    system_prompt = agent.build_system_prompt()

    # Record every SQL the agent runs by wrapping the real run_sql.
    captured = []
    real_run_sql = agent.run_sql

    def spy_run_sql(db_path, query):
        captured.append(query)
        return real_run_sql(db_path, query)

    agent.run_sql = spy_run_sql

    passed = 0
    for i, (question, expect) in enumerate(TESTS, 1):
        captured.clear()
        history = []  # fresh memory per question — tests are independent
        # verify=False: we are the judge here (ground truth), so skip the
        # extra self-check call to keep the test cheap and the output clean.
        answer = agent.ask(question, client, system_prompt, history, verify=False)
        ok = answer_matches(answer, expect)
        passed += ok

        print(f"\n{'='*70}")
        print(f"Q{i}: {question}   [{'PASS' if ok else 'FAIL'}]")
        print(f"EXPECT: {', '.join(expect)}")
        print(f"SQL:    {captured[-1] if captured else '(no SQL run)'}")
        print(f"ANSWER: {answer.strip()}")

    print(f"\n{'='*70}\nScore: {passed}/{len(TESTS)}")
    sys.exit(0 if passed == len(TESTS) else 1)


if __name__ == "__main__":
    main()
