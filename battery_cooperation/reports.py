"""已出具的合作报告不可覆盖，新版本须说明变化来源。

报告按名称保存为不可变版本：每次出具生成新 revision，旧版本内容与输入快照保持不变。
数据库层用触发器禁止更新与删除报告行。
新版本与上一版逐地区、逐口径比较，列出：
- 地区归属变化（哪些批次从哪个国家移到哪个国家）
- 贡献变化（就业、电力、税费、收益、投资、产能的前后值）
- 证据变化（新增/更正的资料及其种类，例如原产地证明、投产日期）
"""
import hashlib
import json
from datetime import datetime, timezone

from .commitments import outstanding
from .contribution import contribution
from .evidence import parse_time, versions_all
from .models import normalize_history

def _now():
    return datetime.now(timezone.utc).isoformat()


def init(db):
    db.execute(
        'CREATE TABLE IF NOT EXISTS reports (name TEXT, revision INTEGER, issued_at TEXT, known_at TEXT, '
        'period_start TEXT, period_end TEXT, body TEXT, digest TEXT, PRIMARY KEY(name,revision))'
    )
    db.execute(
        "CREATE TRIGGER IF NOT EXISTS reports_no_update BEFORE UPDATE ON reports "
        "BEGIN SELECT RAISE(ABORT, '已出具报告不可覆盖'); END"
    )
    db.execute(
        "CREATE TRIGGER IF NOT EXISTS reports_no_delete BEFORE DELETE ON reports "
        "BEGIN SELECT RAISE(ABORT, '已出具报告不可删除'); END"
    )


def _period(period):
    if not isinstance(period, dict) or 'start' not in period or 'end' not in period:
        raise ValueError('报告必须提供期间 start 与 end')
    from .models import day
    start = day(period['start'], 'start', 'report')
    end = day(period['end'], 'end', 'report')
    if start > end:
        raise ValueError('报告期间起始晚于结束')
    return {'start': start, 'end': end}


def _report_body(name, revision, known_at, period, factors, data, changes):
    return {
        'report': name,
        'revision': revision,
        'issued_at': _now(),
        'known_at': known_at,
        'period': period,
        'method': {
            'factors_source': factors.source,
            'factors_configured': factors.configured,
            'scope_separation': '税费、收益、投资、就业、电量、加工量分别合计，不跨口径相加',
        },
        'snapshot': data['contribution']['snapshot'],
        'countries': data['contribution']['countries'],
        'lots': data['contribution']['lots'],
        'flows': data['contribution']['flows'],
        'plants': data['contribution']['plants'],
        'commitments': data['commitments'],
        'pending_documents': data['contribution']['pending_documents'],
        'unverified_documents': data['contribution']['unverified_documents'],
        'origin_issues': data['contribution']['origin_issues'],
        'invalid_documents': data['contribution']['invalid_documents'],
        'fallback_documents': data['contribution']['fallback_documents'],
        'unsupported_documents': data['contribution']['unsupported_documents'],
        'changes': changes,
    }


def _load_latest(db, name):
    row = db.execute(
        'SELECT revision, body, digest FROM reports WHERE name=? ORDER BY revision DESC LIMIT 1', (name,)
    ).fetchone()
    if not row:
        return None
    revision, body, digest = row
    return {'revision': revision, 'body': json.loads(body), 'digest': digest}


def _diff_evidence(previous_snapshot, current_snapshot):
    before = {(row['kind'], row['id']): row for row in previous_snapshot}
    after = {(row['kind'], row['id']): row for row in current_snapshot}
    changes = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old is None:
            changes.append({'kind': key[0], 'id': key[1], 'change': '新增', 'revision': new['revision']})
        elif new is None:
            changes.append({'kind': key[0], 'id': key[1], 'change': '本版不再采纳', 'revision': old['revision']})
        elif old['digest'] != new['digest']:
            changes.append({
                'kind': key[0],
                'id': key[1],
                'change': '更正',
                'from_revision': old['revision'],
                'to_revision': new['revision'],
                'reason': _classification(key[0]),
            })
    return changes


def _classification(kind):
    return {
        'origin_certificate': '原产地证明更正，影响批次的国家归属',
        'plant': '工厂资料更正，可能影响投产日期或所在国家',
        'shipment': '货运批次更正，影响吨位或加工厂',
        'fiscal': '财年口径更正，影响期间归属',
        'commitment': '承诺内容更正，影响未兑现部分',
    }.get(kind, '业务凭证更正')


def _index_countries(rows):
    return {row['country']: row for row in rows}


def _diff_countries(previous, current):
    before = _index_countries(previous)
    after = _index_countries(current)
    changes = []
    for country in sorted(set(before) | set(after)):
        old = before.get(country, {})
        new = after.get(country, {})
        if not old:
            changes.append({'country': country, 'change': '本版新增地区', 'after': _summary(new)})
            continue
        if not new:
            changes.append({'country': country, 'change': '本版不再出现', 'before': _summary(old)})
            continue
        delta = {}
        for key in ('processed_tons', 'capacity_tons_per_year'):
            if old.get(key) != new.get(key):
                delta[key] = {'before': old.get(key), 'after': new.get(key)}
        for key in ('jobs', 'power_mwh', 'tax', 'revenue', 'investment'):
            old_value = (old.get(key) or {}).get('base') if isinstance(old.get(key), dict) else old.get(key)
            new_value = (new.get(key) or {}).get('base') if isinstance(new.get(key), dict) else new.get(key)
            if old_value != new_value:
                delta[key] = {'before': old_value, 'after': new_value}
        if old.get('power_coverage') != new.get('power_coverage'):
            delta['power_coverage'] = {'before': old.get('power_coverage'), 'after': new.get('power_coverage')}
        if delta:
            changes.append({'country': country, 'change': '贡献变化', 'delta': delta})
    return changes


def _summary(row):
    return {
        'processed_tons': row.get('processed_tons'),
        'jobs': (row.get('jobs') or {}).get('base'),
        'power_mwh': (row.get('power_mwh') or {}).get('base'),
        'tax': (row.get('tax') or {}).get('base'),
        'revenue': (row.get('revenue') or {}).get('base'),
        'investment': (row.get('investment') or {}).get('base'),
        'capacity_tons_per_year': row.get('capacity_tons_per_year'),
    }


def _diff_commitments(previous, current):
    before = {row['commitment']: row for row in previous}
    after = {row['commitment']: row for row in current}
    changes = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old is None:
            changes.append({'commitment': key, 'change': '本版新增承诺', 'scope': new['scope'], 'country': new['country']})
        elif new is None:
            changes.append({'commitment': key, 'change': '本版不再列入承诺', 'scope': old['scope'], 'country': old['country']})
        elif old['status'] != new['status'] or old['outstanding'] != new['outstanding']:
            changes.append({
                'commitment': key,
                'change': '兑现状态变化',
                'scope': new['scope'],
                'country': new['country'],
                'status': {'before': old['status'], 'after': new['status']},
                'outstanding': {'before': old['outstanding'], 'after': new['outstanding']},
            })
    return changes


def _diff_regions(previous, current):
    """按批次追踪加工地与原产地的归属变化。"""
    before = {row['lot']: row for row in previous.get('lots') or []}
    after = {row['lot']: row for row in current.get('lots') or []}
    changes = []
    for lot in sorted(set(before) | set(after)):
        old = before.get(lot)
        new = after.get(lot)
        if old is None:
            changes.append({
                'lot': lot,
                'change': '本版新增批次',
                'processed_in': new.get('country'),
                'origin_country': new.get('origin_country'),
                'origin_status': new.get('origin_status'),
            })
            continue
        if new is None:
            changes.append({'lot': lot, 'change': '本版不再列入批次', 'processed_in': old.get('country')})
            continue
        for key, label in (('country', '加工地'), ('origin_country', '原产地')):
            if old.get(key) != new.get(key):
                changes.append({
                    'lot': lot,
                    'change': label + '归属变化',
                    'field': key,
                    'from': old.get(key),
                    'to': new.get(key),
                    'from_origin_status': old.get('origin_status'),
                    'to_origin_status': new.get('origin_status'),
                })
        if old.get('origin_status') != new.get('origin_status'):
            changes.append({
                'lot': lot,
                'change': '原产地状态变化',
                'from_status': old.get('origin_status'),
                'to_status': new.get('origin_status'),
                'origin_country': new.get('origin_country'),
            })
    return changes


def _diff_plants(previous, current):
    """投产日期或所在国家更正时，明确列出前后状态。"""
    before = {row['plant']: row for row in previous}
    after = {row['plant']: row for row in current}
    changes = []
    for plant_id in sorted(set(before) | set(after)):
        old = before.get(plant_id)
        new = after.get(plant_id)
        if old is None:
            changes.append({'plant': plant_id, 'change': '本版新增工厂', 'country': new['country'], 'status': new['status'],
                            'commissioned_on': new['commissioned_on']})
            continue
        if new is None:
            changes.append({'plant': plant_id, 'change': '本版不再列入工厂', 'country': old['country'], 'status': old['status']})
            continue
        delta = {}
        for key in ('country', 'status', 'commissioned_on', 'closure_on', 'capacity_tons_per_year'):
            if old.get(key) != new.get(key):
                delta[key] = {'before': old.get(key), 'after': new.get(key)}
        if old.get('service_regions') != new.get('service_regions'):
            delta['service_regions'] = {'before': old.get('service_regions'), 'after': new.get('service_regions')}
        if delta:
            changes.append({'plant': plant_id, 'change': '工厂状态或投用日期变化', 'delta': delta})
    return changes


def issue(db, name, known_at, period, factors):
    """出具不可变报告；同名再次出具生成新版本并附带变化说明。"""
    init(db)
    period = _period(period)
    parse_time(known_at)
    previous = _load_latest(db, name)
    if previous is not None and parse_time(known_at) < parse_time(previous['body']['known_at']):
        raise ValueError('新版本的 known_at 不得早于上一版，已出具报告不可回退覆盖')
    if previous is not None and previous['body']['period'] != period:
        raise ValueError('同名报告不得改变期间；不同期间请另设报告名称以保持可比')
    data = {'db': db}
    documents = versions_all(db, known_at)
    model = normalize_history(documents)
    result = contribution(model, period, factors)
    result['snapshot'] = sorted(
        ({'kind': row['kind'], 'id': row['id'], 'revision': row['revision'], 'digest': row['digest']}
         for row in model.used_documents),
        key=lambda row: (row['kind'], row['id'], row['revision']),
    )
    commitment_rows = outstanding(model, period, factors, result)
    data['contribution'] = result
    data['commitments'] = commitment_rows

    revision = 1 if previous is None else previous['revision'] + 1
    if previous is None:
        changes = {'from_revision': None, 'note': '首次出具'}
    else:
        previous_body = previous['body']
        changes = {
            'from_revision': previous['revision'],
            'region_changes': _diff_regions(previous_body, result),
            'contribution_changes': _diff_countries(previous_body.get('countries', []), result['countries']),
            'commitment_changes': _diff_commitments(previous_body.get('commitments', []), commitment_rows),
            'plant_changes': _diff_plants(previous_body.get('plants', []), result['plants']),
            'evidence_changes': _diff_evidence(previous_body.get('snapshot', []), result['snapshot']),
            'note': '本版不覆盖旧版；变化来源于上列证据更正或新增',
        }
        if result['fallback_documents']:
            changes['rejected_documents'] = result['fallback_documents']
    body = _report_body(name, revision, known_at, period, factors, data, changes)
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(text.encode()).hexdigest()
    with db:
        db.execute(
            'INSERT INTO reports VALUES (?,?,?,?,?,?,?,?)',
            (name, revision, body['issued_at'], known_at, period['start'], period['end'], text, digest),
        )
    return body


def history(db, name):
    init(db)
    rows = db.execute(
        'SELECT revision, issued_at, known_at, period_start, period_end, body, digest FROM reports WHERE name=? ORDER BY revision',
        (name,),
    ).fetchall()
    return [
        {
            'revision': revision,
            'issued_at': issued_at,
            'known_at': known_at,
            'period': {'start': start, 'end': end},
            'digest': digest,
            'changes': json.loads(body)['changes'],
        }
        for revision, issued_at, known_at, start, end, body, digest in rows
    ]


def read(db, name, revision=None):
    init(db)
    if revision is None:
        row = db.execute(
            'SELECT body, digest FROM reports WHERE name=? ORDER BY revision DESC LIMIT 1', (name,)
        ).fetchone()
    else:
        row = db.execute('SELECT body, digest FROM reports WHERE name=? AND revision=?', (name, revision)).fetchone()
    if not row:
        return None
    return {**json.loads(row[0]), 'digest': row[1]}
