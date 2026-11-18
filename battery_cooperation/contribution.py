"""按地区、按可核实期间计算贡献区间，并单列尚未兑现的承诺。

原则：
- 金额按口径分桶（jobs / tons / power / tax / revenue / investment），量纲不同不相加。
- 就业、电力、税收、收益、投资只统计工厂已投用、期间重叠、凭证可追溯的记录；
  无凭证或期间无法核实的记录进入待核清单，不计入贡献。
- 同一工厂重叠期间的就业记录视为同一口径的重复申报，采用最近收到的版本，
  旧记录保留为"已被更晚版本取代"，不重复加总。
- 每个数字给出 low/base 区间；系数来源和占位状态写进报告方法说明。
"""
from collections import defaultdict
from datetime import date

from .evidence import parse_time
from .lots import country_lots, lots
from .models import load, normalize_period, plant_state, resolve_period

MONEY_SCOPES = ('tax', 'revenue', 'investment')


def days_between(start, end):
    return max(0, (date.fromisoformat(end) - date.fromisoformat(start)).days + 1)


def _overlap(start, end, period):
    return days_between(max(start, period['start']), min(end, period['end']))


def _prorate(value, start, end, period):
    total = days_between(period['start'], period['end'])
    if total <= 0:
        return 0.0
    return value * _overlap(start, end, period) / total


def _country_of(model, plant_id):
    plant = model.plants.get(plant_id)
    if not plant:
        return None, '工厂待核'
    return plant['payload']['country'], None


def _verify(payload):
    reference = payload.get('evidence_ref')
    return isinstance(reference, str) and bool(reference.strip())


def _empty_bucket():
    return {
        'lots': 0,
        'tons': 0.0,
        'jobs': {'low': 0.0, 'base': 0.0},
        'power_mwh': {'low': 0.0, 'base': 0.0},
        'power_coverage': None,
        'tax': {'low': 0.0, 'base': 0.0, 'currencies': {}},
        'revenue': {'low': 0.0, 'base': 0.0, 'currencies': {}},
        'investment': {'low': 0.0, 'base': 0.0, 'currencies': {}},
        'capacity_tons_per_year': 0.0,
        'service_regions': {},
    }


def _period_records(records, period, fiscal):
    placed, pending = [], []
    for record in records:
        span, reason = resolve_period(record['payload'], fiscal)
        if span is None:
            pending.append({'document': record['id'], 'kind': record['kind'], 'reason': reason})
            continue
        if _overlap(span[0], span[1], period) <= 0:
            continue
        placed.append({**record, 'span': span})
    return placed, pending


def _group_key(payload, fields):
    return tuple(payload.get(name) for name in fields)


def _drop_superseded(placed, kind, fields=('plant',)):
    """同工厂同类别、期间重叠时只保留最近收到的记录，避免重复加总。"""
    kept, dropped = [], []
    for record in sorted(placed, key=lambda r: parse_time(r['received_at']), reverse=True):
        clash = next(
            (other for other in kept
             if _group_key(other['payload'], fields) == _group_key(record['payload'], fields)
             and _overlap(record['span'][0], record['span'][1], {'start': other['span'][0], 'end': other['span'][1]}) > 0),
            None,
        )
        if clash:
            dropped.append({
                'document': record['id'],
                'kind': kind,
                'reason': f"与更晚收到的 {clash['id']} 期间重叠，未重复计入",
            })
        else:
            kept.append(record)
    return kept, dropped


def _plant_ready(model, plant_id, period):
    return plant_state(model.plants.get(plant_id), period) == '已投用'


def contribution(model, period, factors, plant=None):
    period = normalize_period(period)
    buckets = defaultdict(_empty_bucket)
    pending_documents = []
    unverified_documents = []

    # 加工量：只算期间内、已投用、原产地已核证的批次。
    for country, rows in country_lots(model, period, plant=plant).items():
        bucket = buckets[country]
        bucket['lots'] = len(rows)
        bucket['tons'] = sum(r['tons'] for r in rows)

    # 产能：铭牌产能按投用/关闭与期间重叠按日折算。
    for plant_id, record in model.plants.items():
        if plant is not None and plant_id != plant:
            continue
        payload = record['payload']
        if payload.get('capacity_tons_per_year') is None or not payload.get('commissioned_on'):
            continue
        start = max(payload['commissioned_on'], period['start'])
        end = period['end']
        if payload.get('closure_on'):
            end = min(end, payload['closure_on'])
        if start > end:
            continue
        buckets[payload['country']]['capacity_tons_per_year'] += _prorate(payload['capacity_tons_per_year'], start, end, period)

    # 服务地区：工厂在该期间实际服务的地区。
    for record in model.service:
        payload = record['payload']
        if plant is not None and payload['plant'] != plant:
            continue
        country, note = _country_of(model, payload['plant'])
        if note:
            pending_documents.append({'document': record['id'], 'kind': 'service', 'reason': note})
            continue
        if not _plant_ready(model, payload['plant'], period):
            pending_documents.append({'document': record['id'], 'kind': 'service', 'reason': '工厂在期间内未投用'})
            continue
        if _overlap(payload['effective_from'], payload['effective_to'] or period['end'], period) <= 0:
            continue
        if not _verify(payload):
            unverified_documents.append({'document': record['id'], 'kind': 'service', 'reason': '缺少凭证引用'})
            continue
        for region in payload['regions']:
            buckets[country]['service_regions'][region] = record['digest']

    # 就业：人数是存量，按重叠期间折算；同厂重叠记录不重复计入。
    employment, pending = _period_records(model.employment, period, model.fiscal)
    pending_documents.extend(pending)
    employment, dropped = _drop_superseded(employment, 'employment')
    pending_documents.extend(dropped)
    for item in employment:
        if plant is not None and item['payload']['plant'] != plant:
            continue
        country, note = _country_of(model, item['payload']['plant'])
        if note:
            pending_documents.append({'document': item['id'], 'kind': 'employment', 'reason': note})
            continue
        if not _plant_ready(model, item['payload']['plant'], period):
            pending_documents.append({'document': item['id'], 'kind': 'employment', 'reason': '工厂在期间内未投用'})
            continue
        if not _verify(item['payload']):
            unverified_documents.extend([{'document': item['id'], 'kind': 'employment', 'reason': '缺少凭证引用'}])
            continue
        scale = _prorate(1.0, item['span'][0], item['span'][1], period)
        bucket = buckets[country]
        bucket['jobs']['low'] += item['payload']['local_employees'] * scale * 0.9
        bucket['jobs']['base'] += item['payload']['local_employees'] * scale

    # 电力：电量与覆盖率分列，覆盖率取期间内最高可核实值。
    power, pending = _period_records(model.power, period, model.fiscal)
    pending_documents.extend(pending)
    power, dropped = _drop_superseded(power, 'power')
    pending_documents.extend(dropped)
    for item in power:
        if plant is not None and item['payload']['plant'] != plant:
            continue
        country, note = _country_of(model, item['payload']['plant'])
        if note:
            pending_documents.append({'document': item['id'], 'kind': 'power', 'reason': note})
            continue
        if not _plant_ready(model, item['payload']['plant'], period):
            pending_documents.append({'document': item['id'], 'kind': 'power', 'reason': '工厂在期间内未投用'})
            continue
        if not _verify(item['payload']):
            unverified_documents.append({'document': item['id'], 'kind': 'power', 'reason': '缺少凭证引用'})
            continue
        supplied = _prorate(item['payload']['supplied_mwh'], item['span'][0], item['span'][1], period)
        bucket = buckets[country]
        bucket['power_mwh']['low'] += supplied * 0.95
        bucket['power_mwh']['base'] += supplied
        ratio = item['payload'].get('coverage_ratio')
        if ratio is not None:
            bucket['power_coverage'] = ratio if bucket['power_coverage'] is None else max(bucket['power_coverage'], ratio)

    # 金额：按口径分桶，不跨口径相加；同时保留原币明细。
    for kind, records in (('tax', model.taxes), ('revenue', model.revenues), ('investment', model.investments)):
        placed, pending = _period_records(records, period, model.fiscal)
        pending_documents.extend(pending)
        placed, dropped = _drop_superseded(placed, kind, fields=('plant', 'category'))
        pending_documents.extend(dropped)
        for item in placed:
            if plant is not None and item['payload']['plant'] != plant:
                continue
            country, note = _country_of(model, item['payload']['plant'])
            if note:
                pending_documents.append({'document': item['id'], 'kind': kind, 'reason': note})
                continue
            if not _plant_ready(model, item['payload']['plant'], period):
                pending_documents.append({'document': item['id'], 'kind': kind, 'reason': '工厂在期间内未投用'})
                continue
            if not _verify(item['payload']):
                unverified_documents.append({'document': item['id'], 'kind': kind, 'reason': '缺少凭证引用'})
                continue
            amount = _prorate(item['payload']['amount'], item['span'][0], item['span'][1], period)
            bucket = buckets[country]
            bucket[kind]['currencies'][item['payload']['currency']] = (
                bucket[kind]['currencies'].get(item['payload']['currency'], 0.0) + amount
            )
            converted, _, reason = factors.convert(kind, amount, item['payload']['currency'])
            if converted is None:
                unverified_documents.append({'document': item['id'], 'kind': kind, 'reason': reason})
                continue
            bucket[kind]['low'] += converted['low']
            bucket[kind]['base'] += converted['base']

    all_lots, origin_issues = lots(model, period)
    if plant is not None:
        all_lots = [row for row in all_lots if row['plant'] == plant]

    notes = [] if factors.configured else [factors.source]
    plants = [_plant_row(model, plant_id, record, period, plant)
              for plant_id, record in sorted(model.plants.items())
              if plant is None or plant_id == plant]
    return {
        'period': dict(period),
        'scope': {'plant': plant} if plant else {'country': '*'},
        'countries': [_finalize(country, bucket) for country, bucket in sorted(buckets.items())],
        'lots': all_lots,
        'flows': _flows(all_lots),
        'plants': plants,
        'origin_issues': origin_issues,
        'pending_documents': pending_documents,
        'unverified_documents': unverified_documents,
        'invalid_documents': model.invalid,
        'fallback_documents': model.fallbacks,
        'unsupported_documents': model.unsupported,
        'notes': notes,
    }


def _flows(rows):
    """采购流向：按原产地与加工地配对汇总，区分已核证与仅申报。"""
    groups = {}
    for row in rows:
        if not row['in_period'] or row['plant_status'] != '已投用':
            continue
        key = (row['origin_country'], row['country'], row['origin_status'])
        entry = groups.setdefault(key, {'origin_country': row['origin_country'], 'processed_in': row['country'],
                                        'origin_status': row['origin_status'], 'lots': 0, 'tons': 0.0})
        entry['lots'] += 1
        entry['tons'] += row['tons']
    return [dict(entry, tons=round(entry['tons'], 3)) for _, entry in sorted(groups.items())]


def _plant_row(model, plant_id, record, period, selected):
    payload = record['payload']
    state = plant_state(record, period)
    regions = sorted({
        region
        for service in model.service
        if service['payload']['plant'] == plant_id and _verify(service['payload'])
        and _overlap(service['payload']['effective_from'], service['payload']['effective_to'] or period['end'], period) > 0
        for region in service['payload']['regions']
    })
    return {
        'plant': plant_id,
        'name': payload.get('name'),
        'country': payload.get('country'),
        'status': state,
        'commissioned_on': payload.get('commissioned_on'),
        'closure_on': payload.get('closure_on'),
        'capacity_tons_per_year': payload.get('capacity_tons_per_year'),
        'service_regions': regions if state == '已投用' else [],
        'source': record['digest'],
    }


def _finalize(country, bucket):
    return {
        'country': country,
        'lots': bucket['lots'],
        'processed_tons': round(bucket['tons'], 3),
        'jobs': {'low': round(bucket['jobs']['low'], 1), 'base': round(bucket['jobs']['base'], 1)},
        'power_mwh': {'low': round(bucket['power_mwh']['low'], 1), 'base': round(bucket['power_mwh']['base'], 1)},
        'power_coverage': bucket['power_coverage'],
        'tax': _money(bucket['tax']),
        'revenue': _money(bucket['revenue']),
        'investment': _money(bucket['investment']),
        'capacity_tons_per_year': round(bucket['capacity_tons_per_year'], 3),
        'service_regions': sorted(bucket['service_regions']),
    }


def _money(bucket):
    return {
        'low': round(bucket['low'], 2),
        'base': round(bucket['base'], 2),
        'original_currency': {currency: round(value, 2) for currency, value in sorted(bucket['currencies'].items())},
    }


def countries(db, known_at, period=None):
    """保留旧接口：按加工地汇总可核实期间的批次。"""
    model = load(db, known_at)
    if period is None:
        movement_days = [r['payload']['processed_on'] or r['payload']['shipped_on'] for r in model.shipments]
        movement_days = [d for d in movement_days if d]
        period = {
            'start': min(movement_days) if movement_days else '0001-01-01',
            'end': max(movement_days) if movement_days else '9999-12-31',
        }
    return [
        {'country': country, 'lots': len(rows), 'processed_tons': sum(r['tons'] for r in rows)}
        for country, rows in sorted(country_lots(model, period).items())
    ]
