from .evidence import at
def shipments(db, known_at):
    return [{'lot':r['id'],**r['payload'],'source':r['digest']} for r in at(db,'shipment',known_at)]
