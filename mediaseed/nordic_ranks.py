"""NordicBytes rank requirements supplied by the user on 2026-10-15.

Account credited upload is deliberately not used as true upload. The current
API has no documented true-upload, join-date, comment or post counters.
Archivist is an alternative seeding path, not the step after Legend.
"""
GIB = 1024**3
TIB = 1024**4
RANKS = (
    ("Explorer", (("trueUpload", 500*GIB, "bytes"), ("membership", 30, "days"))),
    ("Contributor", (("trueUpload", TIB, "bytes"), ("membership", 60, "days"))),
    ("Pirate", (("trueUpload", 5*TIB, "bytes"), ("membership", 90, "days"))),
    ("Veteran", (("trueUpload", 10*TIB, "bytes"), ("membership", 180, "days"))),
    ("Guru", (("trueUpload", 50*TIB, "bytes"), ("comments", 50, "count"), ("posts", 25, "count"), ("membership", 365, "days"))),
    ("Legend", (("trueUpload", 100*TIB, "bytes"), ("comments", 200, "count"), ("posts", 100, "count"), ("membership", 730, "days"))),
    ("Archivist", (("averageSeeding", 60, "days"), ("seedingSize", 10*TIB, "bytes"), ("membership", 90, "days"))),
)


def rank_progress(ranks, stats):
    table = []
    for title, requirements in RANKS:
        items = []
        for metric, goal, unit in requirements:
            # Only seeding size has a confirmed compatible account field.
            current = stats.get("seeding_size") if metric == "seedingSize" else None
            if type(current) not in (int, float) or current < 0:
                current = None
            items.append({"metric": metric, "goal": goal, "unit": unit, "current": current,
                          "missing": max(0, goal-current) if current is not None else None})
        table.append({"title": title, "requirements": items, "alternative": title == "Archivist"})
    title = ranks.get("automatic", "").casefold()
    index = next((i for i, row in enumerate(table) if row["title"].casefold() == title), None)
    # An unlisted current rank must not be assigned a guessed successor.
    target = table[index+1] if index is not None and index < 5 else None
    return {"target": target, "firstListed": table[0] if index is None else None, "table": table, "sourceDate": "2026-10-15",
            "state": "next" if target else "highest_listed" if index == 5 else "alternative" if index == 6 else "unlisted"}
