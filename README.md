# Text-to-SQL Agent

Ask questions about a SQL database in plain English. The agent injects the
database schema into the prompt, lets Claude write SQL via tool-calling,
executes it read-only, and feeds any error back so the model can fix its own
query and retry. Built on the Anthropic Messages API.

```
natural-language question
  -> inject the database schema into the prompt
  -> Claude writes SQL and calls the run_sql tool
  -> execute the SQL (read-only) against the database
  -> hand the result (or the error) back to Claude
  -> on error, Claude reads it, rewrites the SQL, and retries
  -> Claude writes a plain-English answer
  -> a second model pass self-checks the SQL against the question
```

The retry loop is what makes this "agentic": the code never parses SQL or
decides when to stop. Claude does, by choosing whether to call the tool again
or to finish.

## Features

- **Tool-calling loop** — one `run_sql` tool; the model decides when to query and when it's done.
- **Read-only by design (two layers)** — a keyword/`;`-chaining guard rejects anything that isn't a single `SELECT`/`WITH`, and the database is opened in SQLite read-only mode (`mode=ro`). Defense in depth.
- **Self-correction** — execution errors are returned to the model as tool results, so it rewrites and retries instead of failing.
- **Self-check pass** — an LLM-as-judge second call reviews the SQL against the question and flags risky answers (e.g. reading a trend into noise).
- **Transient-error retry** — exponential backoff on 429/529/5xx and dropped connections.
- **Conversation memory** — follow-up questions keep context; `clear` wipes it.
- **Swap databases without editing code** — pass the `.db` path on the command line.

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

cp .env.example .env        # then paste your Anthropic API key into .env
```

Get a key at https://console.anthropic.com. The `.env` file is gitignored and
never committed.

## Usage

Run against the bundled Chinook sample database:

```bash
./.venv/bin/python agent.py
```

Then ask questions in plain English:

```
Question > Which genre has the most tracks?
Question > What is the total revenue from the Rock genre?
```

Commands: `clear` (forget the conversation), `verify on` / `verify off`
(toggle the self-check), `quit`.

### Use your own CSV

No code changes needed — convert the CSV to SQLite, then pass it on the
command line:

```bash
./.venv/bin/python csv_to_db.py mydata.csv   # creates mydata.db
./.venv/bin/python agent.py mydata.db
```

## Testing

Two test scripts measure accuracy against answers computed directly from the
database (ground truth):

- `test_accuracy.py` — 10 questions from single-table counts to 4-table joins.
- `test_adversarial.py` — 8 "tricky" questions probing failure modes: synonym
  traps (length vs price), temporal filters, NULLs, empty results, multi-step
  aggregation, and ambiguous metrics.

```bash
./.venv/bin/python test_accuracy.py
./.venv/bin/python test_adversarial.py
```

On the Chinook benchmark the agent produced correct SQL and matching results
on all 10 clean questions and all 8 adversarial ones. The adversarial set did
surface one behavioral weakness: on ambiguous questions ("best-selling
artist", "best employee") the agent silently picks one metric instead of
stating its assumption or asking — a real risk for a self-serve BI tool.

## Limitations

- **Schema is injected in full**, so very large databases (hundreds of tables) won't fit the prompt — those need table selection / retrieval.
- **`csv_to_db.py` stores every column as TEXT** for simplicity; messy values like `$24.99` or thousands separators would break numeric aggregation.
- **The self-check reduces but does not eliminate errors** — the judge shares a model family with the agent and can share blind spots.
- **Large result sets are not capped** — a query returning tens of thousands of rows is sent back in full.

## Stack

Python · Anthropic Messages API (`claude-sonnet-4-6`) · SQLite · tool-calling
