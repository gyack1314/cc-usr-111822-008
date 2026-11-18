from collections import defaultdict
from .factories import production
def countries(db, known_at):
    groups=defaultdict(list)
    for row in production(db,known_at):
        if 'country' in row:groups[row['country']].append(row)
    return [{'country':country,'lots':len(rows),'processed_tons':sum(r['tons'] for r in rows)} for country,rows in sorted(groups.items())]
