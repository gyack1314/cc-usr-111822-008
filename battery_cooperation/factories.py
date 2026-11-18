from .evidence import at
from .materials import shipments
def production(db, known_at):
    plants={r['id']:r for r in at(db,'plant',known_at)};out=[]
    for row in shipments(db,known_at):
        plant=plants.get(row['plant'])
        if not plant:out.append({'lot':row['lot'],'status':'工厂待核'});continue
        out.append({**row,'country':plant['payload']['country'],'plant_source':plant['digest']})
    return out
