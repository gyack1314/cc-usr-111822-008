"""对照承诺与已实现贡献，给出尚未兑现部分。

承诺与实现按同一口径比较，但只在目标日期落入报告期时才判定兑现：
- 目标日期晚于报告期结束：状态"未到期"，只列公告目标，不把任何实现扣减为兑现。
- 目标日期早于报告期开始：状态"需按目标期报告核对"，避免用别的期间的数字判断。
- 目标日期在报告期内：用本报告期同口径可核实实现比较，给出未兑现部分。
承诺写明工厂时按该工厂的实现比较，不拿全国合计充抵单厂承诺。
金额承诺保留原币和折算基准两种表述，便于合作方核对。
"""
from .contribution import contribution

SCOPE_LABEL = {
    'jobs': '当地就业',
    'tax': '税费',
    'revenue': '收益',
    'power': '电力',
    'capacity': '产能',
    'investment': '投资',
}


def _plant_country(model, plant_id):
    plant = model.plants.get(plant_id)
    return plant['payload']['country'] if plant else None


def _commitment_country(model, record):
    payload = record['payload']
    if payload.get('country'):
        return payload['country']
    return _plant_country(model, payload['plant'])


def _realized(result, country, scope):
    for row in result['countries']:
        if row['country'] != country:
            continue
        if scope == 'jobs':
            return row['jobs']['base']
        if scope == 'power':
            return row['power_mwh']['base']
        if scope == 'capacity':
            return row['capacity_tons_per_year']
        return row[scope]['base']
    return 0.0


def _scoped_result(model, period, factors, result, plant):
    if not plant:
        return result
    return contribution(model, period, factors, plant=plant)


def outstanding(model, period, factors, result):
    rows = []
    for record in model.commitments:
        payload = record['payload']
        country = _commitment_country(model, record)
        row = {
            'commitment': record['id'],
            'label': payload.get('label'),
            'scope': payload['scope'],
            'scope_label': SCOPE_LABEL[payload['scope']],
            'country': country,
            'plant': payload.get('plant'),
            'target_date': payload['target_date'],
            'target': payload['target'],
            'target_currency': payload.get('currency'),
        }
        if country is None:
            rows.append({**row, 'status': '地区待核', 'realized': None, 'outstanding': None})
            continue
        comparison = payload['target']
        note = None
        if payload['scope'] in ('tax', 'revenue', 'investment'):
            if not payload.get('currency'):
                note = '承诺缺少币种'
            else:
                converted, _, reason = factors.convert(payload['scope'], payload['target'], payload['currency'])
                if converted is None:
                    note = reason
                else:
                    comparison = converted['base']
        scoped = _scoped_result(model, period, factors, result, payload.get('plant'))
        realized = _realized(scoped, country, payload['scope'])
        gap = max(0.0, comparison - realized)

        if payload['target_date'] > period['end']:
            status, outstanding_value = '未到期', None
        elif payload['target_date'] < period['start']:
            status, outstanding_value = '需按目标期报告核对', None
            note = note or '目标日期早于本报告期，不能用本期数字判定兑现'
        elif note:
            status, outstanding_value = note, None
        else:
            outstanding_value = gap
            status = '已兑现' if gap <= 1e-9 else '未兑现'

        rows.append({
            **row,
            'status': status,
            'target_compared': round(comparison, 3),
            'realized': round(realized, 3),
            'outstanding': round(outstanding_value, 3) if outstanding_value is not None else None,
            'gap_to_target': round(gap, 3),
            'note': note,
        })
    return rows
