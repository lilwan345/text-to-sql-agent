# Text-to-SQL Agent

A small project I built to learn how AI agents actually work. You ask a
question in plain English, and it writes the SQL, runs it, and tells you the
answer. If the SQL is wrong, it reads the error and fixes it itself.

I'm a business student who's comfortable with SQL, so I wanted to see what it
takes to put an LLM in front of a database instead of writing every query by
hand.

## What it does

```
You ask: "Which genre has the most tracks?"
  -> the agent gets the database structure (table and column names)
  -> Claude writes the SQL and asks to run it
  -> the code runs it on the database (read-only)
  -> if it errors, the error goes back to Claude and it tries again
  -> you get a plain-English answer
```

The part I think is cool: I don't parse the SQL or decide when to stop. Claude
does that itself by choosing whether to run another query or just answer. That's
what makes it an "agent" and not just one API call.

## Things I added while learning

- **It can only read, never change the data.** I check the query is a plain
  `SELECT` before running it, and I also open the database in read-only mode so
  even if something slips past my check, SQLite blocks it. (Two checks because I
  didn't fully trust my first one.)
- **It fixes its own mistakes.** When a query errors, I send the error message
  back to the model so it can rewrite the SQL instead of just crashing.
- **A second "double-check" pass.** After it answers, I make one more call that
  looks at the question + the SQL and flags it if something seems off. It
  doesn't catch everything, but it turns a silently-wrong answer into one with a
  warning.
- **It retries when Anthropic's servers are busy** (I kept hitting "overloaded"
  errors, so I added a wait-and-retry).
- **It remembers the conversation**, so you can ask follow-ups. Type `clear` to
  start over.

## How to run it

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

cp .env.example .env        # paste your Anthropic API key into .env
```

Get a key at https://console.anthropic.com. The `.env` file is gitignored so
the key never ends up on GitHub.

Then:

```bash
./.venv/bin/python agent.py
```

It comes with the Chinook sample database (a fake music store), so you can ask
things like:

```
Question > How many customers are from the USA?
Question > What is the total revenue from the Rock genre?
```

Commands: `clear` (forget the conversation), `verify on` / `verify off`
(turn the double-check on/off), `quit`.

### Trying it on your own CSV

You don't have to touch the code. Turn the CSV into a database file, then point
the agent at it:

```bash
./.venv/bin/python csv_to_db.py mydata.csv   # makes mydata.db
./.venv/bin/python agent.py mydata.db
```

## Testing how good it actually is

I didn't want to just trust that it works, so I wrote two test scripts. I
computed the correct answers myself by querying the database directly, then
checked the agent against them.

- `test_accuracy.py` — 10 normal questions, from simple counts up to joins
  across 4 tables.
- `test_adversarial.py` — 8 tricky ones meant to trip it up (e.g. "longest
  track" where it has to use duration not price, a question about a country with
  no data, and vague questions like "who's the best employee?").

It got all 10 normal ones right and the SQL on all 8 tricky ones too. But the
tricky set showed a real weakness: when a question is vague ("best-selling
artist" — by number sold or by money?), it just quietly picks one and answers
confidently instead of saying which one it chose or asking. For a real BI tool
that's a problem, because different people mean different things by "best".

## Stuff it can't do well (yet)

- It pastes the whole database structure into the prompt, so a giant database
  with hundreds of tables wouldn't fit.
- The CSV converter stores everything as text, so messy values like `$24.99`
  would break math on them.
- The double-check uses the same kind of model, so it can miss the same things
  the agent misses.
- It doesn't limit how many rows come back, so a huge result would all get sent
  at once.

## Built with

Python, the Anthropic API (Claude), and SQLite.
