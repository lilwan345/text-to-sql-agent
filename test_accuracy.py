"""
test_accuracy.py — run a fixed set of questions through the agent and print
each generated SQL + answer, so we can score accuracy against known-correct
("ground truth") values computed directly from the database.

Run:  ./.venv/bin/python test_accuracy.py
Tests against Chinook.db (the default DB_PATH in agent.py).
"""

import anthropic
import agent  # reuse the real agent code — same prompt, same loop

# 10 questions, easy -> hard, with the ground-truth answer we computed by
# querying the DB directly (see the SQL in the chat). The "expect" string is
# just what we eyeball the agent's answer against.
TESTS = [
    ("How many tracks are in the database?",                       "3503"),
    ("How many customers are there?",                              "59"),
    ("How many customers are from the USA?",                       "13"),
    ("How many tracks are longer than 5 minutes?",                 "1069"),
    ("Which genre has the most tracks, and how many?",             "Rock, 1297"),
    ("What is the total revenue across all invoices?",             "2328.60"),
    ("Which billing country has the highest total revenue?",       "USA, 523.06"),
    ("How many albums does the artist AC/DC have?",                "2"),
    ("What is the total revenue from the Rock genre?",             "826.65"),
    ("Which artist generated the most revenue, and how much?",     "Iron Maiden, 138.60"),
]


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

    for i, (question, expect) in enumerate(TESTS, 1):
        captured.clear()
        history = []  # fresh memory per question — tests are independent
        # verify=False: we are the judge here (ground truth), so skip the
        # extra self-check call to keep the test cheap and the output clean.
        answer = agent.ask(question, client, system_prompt, history, verify=False)

        print(f"\n{'='*70}")
        print(f"Q{i}: {question}")
        print(f"EXPECT: {expect}")
        print(f"SQL:    {captured[-1] if captured else '(no SQL run)'}")
        print(f"ANSWER: {answer.strip()}")


if __name__ == "__main__":
    main()
