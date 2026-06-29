"""
test_adversarial.py — harder, "tricky" questions meant to EXPOSE failure
modes, not to be answered cleanly. Run after test_accuracy.py.

These probe: synonym traps (length vs price), temporal filters, ambiguous
metrics (units vs revenue), NULL handling, empty results, multi-step
aggregation, and an unanswerable/ambiguous question that SHOULD trigger a
clarification instead of a confident guess.

For these, "correct" is not always an exact number — sometimes the right
behavior is to STATE AN ASSUMPTION or ASK. We print the SQL + answer and
judge by hand.

Run:  ./.venv/bin/python test_adversarial.py
"""

import anthropic
import agent

TESTS = [
    ("What is the longest track?",
     "trap: length = Milliseconds, NOT price -> 'Occupation / Precipice' (~88 min)"),
    ("Which customer has spent the most money?",
     "join Customer+Invoice, sum -> Helena Holy, $49.62"),
    ("How much revenue did we make in 2023?",
     "temporal: parse InvoiceDate year -> $469.58"),
    ("Who is the best-selling artist?",
     "AMBIGUOUS units vs revenue; good = state which metric (both -> Iron Maiden)"),
    ("How many tracks have no genre assigned?",
     "NULL handling -> 0"),
    ("Show me the sales from Antarctica.",
     "empty result; good = say none/0, do NOT fabricate"),
    ("What is the average number of tracks per album?",
     "multi-step: count per album, then average -> 10.1"),
    ("Who is the best employee?",
     "UNANSWERABLE/ambiguous; good = ask for a criterion, do NOT invent one"),
]


def main():
    client = anthropic.Anthropic()
    system_prompt = agent.build_system_prompt()

    captured = []
    real_run_sql = agent.run_sql

    def spy_run_sql(db_path, query):
        captured.append(query)
        return real_run_sql(db_path, query)

    agent.run_sql = spy_run_sql

    for i, (question, note) in enumerate(TESTS, 1):
        captured.clear()
        history = []
        answer = agent.ask(question, client, system_prompt, history, verify=False)

        print(f"\n{'='*70}")
        print(f"H{i}: {question}")
        print(f"GOOD BEHAVIOR: {note}")
        print(f"SQL RUN ({len(captured)}): {captured[-1].strip() if captured else '(no SQL — asked/clarified)'}")
        print(f"ANSWER: {answer.strip()}")


if __name__ == "__main__":
    main()
