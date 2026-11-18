"""把货物流向落到分批次的加工地、原产地和可核实期间。"""
from collections import defaultdict
from .evidence import parse_time
from .models import plant_state


def _certificate_for(payload, record, certificates):
    """取已收到版本中最近签发、且覆盖该批次的产地证明。

    产地证明可以晚于报告期签发，用于更正历史批次；是否可采用由资料的已知时间决定，
    不由签发日期决定。签发日期单独输出，便于报告说明更正来源。
    """
    candidates = []
    movement = payload.get('processed_on') or payload.get('shipped_on')
    for cert in certificates:
        detail = cert['payload']
        if detail['lot'] != payload['lot']:
            continue
        if detail.get('valid_until') and movement and detail['valid_until'] < movement:
            continue
        candidates.append((detail['issued_on'], parse_time(record['received_at']), cert['revision'], cert))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[:3])[3]


def lots(model, period):
    """返回逐批次流向。期间无法核实或工厂未投用的批次不进入贡献。"""
    output = []
    for record in model.shipments:
        payload = record['payload']
        plant = model.plants.get(payload['plant'])
        state = plant_state(plant, period)
        movement = payload.get('processed_on') or payload.get('shipped_on')
        period_note = None
        if movement is None:
            period_note = '缺少加工或发运日期，无法归属期间'
        elif not (period['start'] <= movement <= period['end']):
            period_note = '加工或发运日期不在报告期间'
        certificate = _certificate_for(payload, record, model.certificates)
        output.append({
            'lot': payload['lot'],
            'declared_origin': payload['origin'],
            'origin_country': certificate['payload']['origin_country'] if certificate else payload['origin'],
            'origin_status': '已核证' if certificate else '仅申报',
            'origin_certificate': certificate['digest'] if certificate else None,
            'origin_evidence_on': certificate['payload']['issued_on'] if certificate else None,
            'plant': payload['plant'],
            'country': plant['payload']['country'] if plant else None,
            'plant_status': state,
            'tons': payload['tons'],
            'movement_date': movement,
            'destination': payload['destination'],
            'in_period': period_note is None,
            'period_note': period_note,
        })
    matched = {row['lot'] for row in output}
    unmatched = [
        {'certificate': cert['id'], 'lot': cert['payload']['lot'], 'reason': '原产地证明未匹配到货运批次'}
        for cert in model.certificates
        if cert['payload']['lot'] not in matched
    ]
    return output, unmatched


def counted(model, period, plant=None):
    """可计入期间贡献的批次：期间内、工厂已投用。原产地另按核证状态标注。"""
    rows, _ = lots(model, period)
    return [row for row in rows if row['in_period'] and row['plant_status'] == '已投用' and (plant is None or row['plant'] == plant)]


def country_lots(model, period, plant=None):
    groups = defaultdict(list)
    for row in counted(model, period, plant=plant):
        groups[row['country']].append(row)
    return dict(groups)
