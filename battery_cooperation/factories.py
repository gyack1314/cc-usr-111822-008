"""按加工厂归属货运批次。"""
from .models import load
from .models import plant_state


def production(db, known_at, period=None):
    model = load(db, known_at)
    if period is None:
        period = {'start': '0001-01-01', 'end': '9999-12-31'}
    out = []
    for record in model.shipments:
        payload = record['payload']
        plant = model.plants.get(payload['plant'])
        state = plant_state(plant, period)
        out.append({
            'lot': payload['lot'],
            'origin': payload['origin'],
            'country': plant['payload']['country'] if plant else None,
            'plant': payload['plant'],
            'status': state,
            'tons': payload['tons'],
            'plant_source': plant['digest'] if plant else None,
            'source': record['digest'],
        })
    return out
