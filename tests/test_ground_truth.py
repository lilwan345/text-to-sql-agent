"""
The eval scripts score the agent against hand-computed answers. This test
recomputes those answers straight from Chinook.db, so a typo in an expected
value can't quietly turn a right answer into a "failure" (or the reverse).
"""

import os
import sqlite3

import pytest

import eval_accuracy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# One reference query per question in eval_accuracy.TESTS, in the same order.
REFERENCE_SQL = [
    "SELECT COUNT(*) FROM Track",
    "SELECT COUNT(*) FROM Customer",
    "SELECT COUNT(*) FROM Customer WHERE Country = 'USA'",
    "SELECT COUNT(*) FROM Track WHERE Milliseconds > 5 * 60 * 1000",
    """SELECT g.Name, COUNT(*) FROM Track t JOIN Genre g USING (GenreId)
       GROUP BY g.Name ORDER BY 2 DESC LIMIT 1""",
    "SELECT ROUND(SUM(Total), 2) FROM Invoice",
    """SELECT BillingCountry, ROUND(SUM(Total), 2) FROM Invoice
       GROUP BY 1 ORDER BY 2 DESC LIMIT 1""",
    """SELECT COUNT(*) FROM Album a JOIN Artist r USING (ArtistId)
       WHERE r.Name = 'AC/DC'""",
    """SELECT ROUND(SUM(il.UnitPrice * il.Quantity), 2)
       FROM InvoiceLine il JOIN Track t USING (TrackId) JOIN Genre g USING (GenreId)
       WHERE g.Name = 'Rock'""",
    """SELECT r.Name, ROUND(SUM(il.UnitPrice * il.Quantity), 2)
       FROM InvoiceLine il JOIN Track t USING (TrackId)
       JOIN Album a USING (AlbumId) JOIN Artist r USING (ArtistId)
       GROUP BY r.Name ORDER BY 2 DESC LIMIT 1""",
]


@pytest.mark.parametrize(
    "case, sql", list(zip(eval_accuracy.TESTS, REFERENCE_SQL)), ids=lambda x: str(x)[:40]
)
def test_expected_answers_match_the_database(case, sql):
    question, expected = case
    conn = sqlite3.connect(f"file:{os.path.join(ROOT, 'Chinook.db')}?mode=ro", uri=True)
    row = conn.execute(sql).fetchone()
    conn.close()
    as_text = ", ".join(str(v) for v in row)
    assert eval_accuracy.answer_matches(as_text, expected), (question, as_text)


def test_every_question_has_a_reference_query():
    assert len(REFERENCE_SQL) == len(eval_accuracy.TESTS)


@pytest.mark.parametrize(
    "answer, expected, ok",
    [
        ("There are 3,503 tracks.", ["3503"], True),
        ("Total revenue is $2,328.6.", ["2328.60"], True),
        ("Rock leads with 1297 tracks.", ["rock", "1297"], True),
        ("Rock leads with 1296 tracks.", ["Rock", "1297"], False),
        ("Metal leads with 1297 tracks.", ["Rock", "1297"], False),
        ("I'm not sure.", ["59"], False),
    ],
)
def test_answer_matching(answer, expected, ok):
    assert eval_accuracy.answer_matches(answer, expected) is ok
