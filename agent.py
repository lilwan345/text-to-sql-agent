"""
Text-to-SQL agent — minimal version (v1).

Pipeline:
  natural-language question
    -> inject the database schema into the prompt
    -> Claude writes SQL and calls the `run_sql` tool
    -> we execute the SQL against Chinook.db
    -> we hand the result (or the error) back to Claude
    -> on error, Claude sees the error message and retries automatically
    -> when Claude is satisfied, it writes a plain-English answer

The retry loop is the whole point of "agentic" here: we don't parse SQL
ourselves or decide when to stop. Claude does, by choosing whether to call
the tool again or to finish talking.
"""

import os
import sqlite3
import sys
import time

import anthropic
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 0. Config
# ---------------------------------------------------------------------------
# load_dotenv() reads a `.env` file in this folder and puts its values into
# the environment. We keep the API key OUT of the source code so it never
# gets committed to git. The .env file is listed in .gitignore.
load_dotenv()

DB_PATH = "Chinook.db"
MODEL = "claude-sonnet-4-6"  # you asked for claude-sonnet
MAX_STEPS = 6                # safety cap so the retry loop can't run forever
MAX_RETRIES = 4             # how many times to retry when the server is busy


# ---------------------------------------------------------------------------
# 1. Read the schema out of the SQLite file
# ---------------------------------------------------------------------------
def get_schema(db_path: str) -> str:
    """
    Return the CREATE TABLE statements for every table in the database.

    Why: Claude has never seen this specific database. If we just ask it to
    "write SQL", it will guess table and column names. Instead we paste the
    real schema (the DDL) into the prompt so it writes SQL that actually fits.

    sqlite_master is SQLite's built-in catalog table. Each row's `sql` column
    holds the exact CREATE statement used to make that table.
    """
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
    ).fetchall()
    conn.close()
    # rows looks like [("CREATE TABLE ...",), ("CREATE TABLE ...",), ...]
    return "\n\n".join(r[0] for r in rows)


# ---------------------------------------------------------------------------
# 2. The one tool Claude is allowed to call
# ---------------------------------------------------------------------------
# Keywords that modify data or schema. This agent is read-only by design,
# so we refuse to run any statement containing them.
_FORBIDDEN = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "CREATE", "REPLACE", "TRUNCATE", "ATTACH", "PRAGMA",
)


def assert_read_only(query: str) -> None:
    """
    Raise ValueError unless `query` is a single, read-only SELECT.

    This is the FIRST of two safety layers (the second is opening the
    database read-only inside run_sql). We check here too because a clear
    error message ("Only SELECT queries are allowed") gets fed back to
    Claude, which then knows to rewrite its SQL.

    Rules:
      1. No statement chaining — a `;` in the middle could hide a second
         command like  SELECT 1; DROP TABLE Track  so we reject it.
      2. The statement must start with SELECT or WITH (WITH = a CTE, which
         is still a read-only query).
      3. It must not contain any write/DDL keyword from _FORBIDDEN.
    """
    cleaned = query.strip().rstrip(";").strip()
    if ";" in cleaned:
        raise ValueError("Only a single statement is allowed (no ';').")

    first_word = cleaned.split(None, 1)[0].upper() if cleaned else ""
    if first_word not in ("SELECT", "WITH"):
        raise ValueError("Only SELECT queries are allowed.")

    # Pad with spaces so we match whole words: " CREATE " won't trip on a
    # column name like "CreateDate".
    padded = f" {cleaned.upper()} "
    for keyword in _FORBIDDEN:
        if f" {keyword} " in padded:
            raise ValueError(f"Write/DDL keyword '{keyword}' is not allowed.")


def run_sql(db_path: str, query: str) -> str:
    """
    Execute one read-only SQL query and return the rows as readable text.

    We open a fresh connection per call (simple and safe). If the SQL is
    invalid, sqlite3 raises an exception — we let it bubble up so the caller
    can catch it and report the error back to Claude.
    """
    # Safety layer 1: reject anything that isn't a plain SELECT.
    assert_read_only(query)

    # Safety layer 2: open the database in READ-ONLY mode (mode=ro). Even if
    # a write somehow slipped past the check above, SQLite itself would now
    # refuse it. Defense in depth.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = conn.execute(query)
        column_names = [d[0] for d in cursor.description]  # header row
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        return "(query ran successfully but returned 0 rows)"

    # Build a simple text table: header line, then one line per row.
    lines = [" | ".join(column_names)]
    for row in rows:
        lines.append(" | ".join(str(value) for value in row))
    return "\n".join(lines)


# This is the JSON description Claude reads to know the tool exists, what it
# does, and what arguments it takes. The `input_schema` is JSON Schema — it
# tells Claude that calling the tool requires a single string field `query`.
TOOLS = [
    {
        "name": "run_sql",
        "description": (
            "Execute a read-only SQL query against the Chinook SQLite "
            "database and return the result rows. Use standard SQLite syntax."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A single valid SQLite SELECT statement.",
                }
            },
            "required": ["query"],
        },
    }
]


# ---------------------------------------------------------------------------
# 3. The agent loop
# ---------------------------------------------------------------------------
def build_system_prompt() -> str:
    """
    Build the system prompt once: the rules + the injected schema. The schema
    never changes, so we build this a single time at startup and reuse it for
    every question instead of rebuilding it on each call.
    """
    schema = get_schema(DB_PATH)
    return (
        "You are a careful data analyst. You answer questions by querying a "
        "SQLite database using the run_sql tool. Always base your final "
        "answer on actual query results — never invent numbers. If a query "
        "errors, read the error, fix the SQL, and try again. Always respond "
        "in English.\n\n"
        "Here is the database schema:\n\n" + schema
    )


def call_claude(client, **kwargs):
    """
    Call the Messages API, but retry when Anthropic's servers are temporarily
    busy. Transient problems (429 = rate limited, 529 = overloaded, 5xx =
    server error, or a dropped connection) usually clear up if we wait a
    moment, so we back off and try again — 1s, then 2s, then 4s.

    Real errors (e.g. 400 bad request, 401 bad API key) are NOT transient —
    retrying would fail the same way — so we re-raise those immediately.
    """
    for attempt in range(MAX_RETRIES):
        try:
            return client.messages.create(**kwargs)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            status = getattr(exc, "status_code", None)
            transient = isinstance(exc, anthropic.APIConnectionError) or (
                status in (429, 500, 502, 503, 504, 529)
            )
            # Give up if it's a "real" error, or if we've used our last try.
            if not transient or attempt == MAX_RETRIES - 1:
                raise
            wait = 2 ** attempt  # 1, 2, 4 seconds
            print(f"  [server busy ({status}); retrying in {wait}s...]")
            time.sleep(wait)


def verify_answer(client, question: str, sqls: list, answer: str) -> str:
    """
    A second "judge" pass over the agent's own work.

    We give a fresh model call the user's question, the SQL that was actually
    run, and the answer that was produced, plus the schema, and ask it to
    decide whether the SQL faithfully answers the question — right columns,
    correct filters/joins/aggregations, and whether the answer is reading a
    trend into what is really just noise.

    This is the LLM-as-judge / self-critique pattern. It does NOT guarantee
    correctness (the same model family can share blind spots), but it turns
    "silently wrong" into "wrong with a visible warning".

    The judge is told to start its reply with OK or WARN so the caller can
    tell whether to flag it.
    """
    schema = get_schema(DB_PATH)
    judge_system = (
        "You are a strict reviewer of text-to-SQL answers. Use the schema "
        "below to check that the SQL uses the right columns and tables.\n\n"
        + schema
    )
    sql_text = "\n\n".join(sqls) if sqls else "(no SQL was run)"
    judge_prompt = (
        "Decide whether the SQL faithfully answers the user's question. "
        "Check: correct columns (e.g. track length is Milliseconds, not "
        "UnitPrice), correct filters/joins/aggregations, and whether the "
        "answer over-interprets small differences as a real trend.\n\n"
        "Reply in this format:\n"
        "  First word: OK (faithfully answers) or WARN (mismatch or risky "
        "assumption).\n"
        "  Then ONE short sentence naming the specific issue (or confirming "
        "it looks right).\n\n"
        f"QUESTION:\n{question}\n\n"
        f"SQL THAT WAS RUN:\n{sql_text}\n\n"
        f"ANSWER GIVEN:\n{answer}"
    )
    response = call_claude(
        client,
        model=MODEL,
        max_tokens=300,
        system=judge_system,
        messages=[{"role": "user", "content": judge_prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


def format_verdict(verdict: str) -> str:
    """Turn the judge's raw reply into a labelled block shown under the answer."""
    upper = verdict.upper()
    if upper.startswith("WARN"):
        return "⚠️  Self-check: " + verdict[4:].lstrip(" :-").strip()
    if upper.startswith("OK"):
        return "✓ Self-check passed: " + verdict[2:].lstrip(" :-").strip()
    return "Self-check: " + verdict  # judge didn't follow the format; show as-is


def ask(
    question: str,
    client,
    system_prompt: str,
    history: list,
    verify: bool = True,
) -> str:
    """
    Run one question through the agent loop.

    `history` is the running conversation, kept ALIVE across questions — this
    is the "memory". We append this turn's messages to it in place, so a
    follow-up like "redo it" or "break it down by year" still has context.
    The caller can wipe `history` (the 'clear' command) to start fresh.
    """
    # Add the new question onto the ongoing conversation.
    history.append({"role": "user", "content": question})

    sqls_run = []  # every SQL executed this turn — fed to the self-check

    for step in range(MAX_STEPS):
        # --- Call Claude ---
        response = call_claude(
            client,
            model=MODEL,
            max_tokens=2048,
            system=system_prompt,
            tools=TOOLS,
            messages=history,
        )

        # We must append Claude's full reply (which may contain text AND a
        # tool_use block) back into the conversation before we respond to it.
        history.append({"role": "assistant", "content": response.content})

        # stop_reason == "tool_use" means Claude wants us to run a tool.
        # Anything else (usually "end_turn") means it's done and has written
        # its final answer, so we break out and return that text.
        if response.stop_reason != "tool_use":
            # Pull out the plain-text blocks of the final answer.
            answer = "".join(
                block.text for block in response.content if block.type == "text"
            )
            # Self-check: have a second model pass judge the SQL vs. the
            # question, and append its verdict so risky answers get flagged.
            if verify and sqls_run:
                verdict = verify_answer(client, question, sqls_run, answer)
                answer += "\n\n" + format_verdict(verdict)
            return answer

        # --- Handle every tool call in this turn ---
        # (Claude usually makes one, but the API allows several at once.)
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            sql = block.input["query"]
            sqls_run.append(sql)  # remember it for the self-check
            print(f"\n[step {step + 1}] Claude is running SQL:\n  {sql}\n")

            # Try to run the SQL. If it fails, we send the error text back
            # with is_error=True so Claude knows to fix it and retry.
            try:
                result_text = run_sql(DB_PATH, sql)
                is_error = False
            except Exception as exc:  # e.g. wrong column name, bad syntax
                result_text = f"SQL error: {exc}"
                is_error = True
                print(f"  -> error: {exc}")

            # A tool_result must reference the tool_use it answers, by id.
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                    "is_error": is_error,
                }
            )

        # Feed the tool result(s) back as a new user turn, then loop so
        # Claude can read them and either retry or write the final answer.
        history.append({"role": "user", "content": tool_results})

    # If we burned through MAX_STEPS without Claude finishing, bail out.
    return "(stopped: reached the maximum number of steps without a final answer)"


# ---------------------------------------------------------------------------
# 4. Run it from the command line
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Optional: pass a database file on the command line, e.g.
    #     python agent.py sales.db
    # to point the agent at your own data WITHOUT editing this file. With no
    # argument it falls back to the default DB_PATH (Chinook.db) set up top.
    if len(sys.argv) > 1:
        DB_PATH = sys.argv[1]
    if not os.path.exists(DB_PATH):
        print(f"Database file not found: {DB_PATH}")
        print("Pass a valid .db path, e.g.  python agent.py sales.db")
        sys.exit(1)

    # Set these up ONCE, then reuse them for every question:
    client = anthropic.Anthropic()       # reads ANTHROPIC_API_KEY from .env
    system_prompt = build_system_prompt()  # rules + schema (built once)
    history = []                          # the conversation memory (grows)
    verify_on = True                      # run the self-check after each answer

    # Interactive mode: keep reading questions until the user quits.
    # Each question runs the full agent loop and REMEMBERS the previous ones,
    # so you can ask follow-ups. Type 'clear' to wipe that memory.
    print(f"Text-to-SQL agent — database: {DB_PATH}")
    print("Ask anything about the data in plain English.")
    print("Commands:  'clear' = forget the conversation,")
    print("           'verify off' / 'verify on' = toggle the self-check,")
    print("           'quit' = exit.\n")

    while True:
        try:
            question = input("Question > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not question:
            continue  # empty line — just prompt again

        command = question.lower()
        if command in ("quit", "exit", "q"):
            print("Bye!")
            break
        if command == "clear":
            history.clear()  # drop all remembered messages; cost resets too
            print("Memory cleared — starting fresh.\n")
            continue
        if command in ("verify on", "verify off"):
            verify_on = command.endswith("on")
            print(f"Self-check is now {'ON' if verify_on else 'OFF'}.\n")
            continue

        # Run the agent. We wrap it so an API error (or a conversation left in
        # a weird state) doesn't crash the whole program — you can 'clear' and
        # keep going.
        try:
            answer = ask(question, client, system_prompt, history, verify_on)
            print("\nA:", answer, "\n")
        except Exception as exc:
            print(f"\n[error] {exc}")
            print("  If this keeps happening, type 'clear' to reset.\n")
