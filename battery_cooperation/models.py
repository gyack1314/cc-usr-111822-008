"""把外部资料规范化为可核实的业务记录。

资料种类：
plant              工厂（所在国家、投用日期、停用日期、铭牌产能）
shipment           货运腿（批次、原产地申报、加工厂、吨位、加工/发运日期）
origin_certificate 原产地证明（按批次更正原产地）
employment         就业凭证（工厂、期间、当地员工数）
tax / revenue      税费与收益凭证（工厂、期间、金额、币种）
power              电力凭证（工厂、期间、供电量、覆盖率）
service            服务地区（工厂、生效期间、覆盖地区、凭证）
commitment         对外承诺（就业、税收、收益、电力、产能、投资）
fiscal             财年口径（辖区、财年、起止日期）
"""
from dataclasses import dataclass, field
from datetime import date
from math import isfinite

SUPPORTED_KINDS = (
    'plant',
    'shipment',
    'origin_certificate',
    'employment',
    'tax',
    'revenue',
    'power',
    'service',
    'commitment',
    'fiscal',
    'investment',
)

MONEY_SCOPES = ('tax', 'revenue', 'investment')
SCOPES = ('jobs', 'tax', 'revenue', 'power', 'capacity', 'investment')


def day(value, name, ident):
    try:
        text = str(value)
        if len(text) < 10:
            raise ValueError
        return date.fromisoformat(text[:10]).isoformat()
    except (TypeError, ValueError):
        raise ValueError(f'{ident} 字段 {name} 不是有效日期') from None


def text(value, name, ident):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{ident} 字段 {name} 必须为非空文本')
    return value.strip()


def number(value, name, ident, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{ident} 字段 {name} 必须为数字')
    if not isfinite(value):
        raise ValueError(f'{ident} 字段 {name} 必须为有限数字')
    if minimum is not None and value < minimum:
        raise ValueError(f'{ident} 字段 {name} 不得小于 {minimum}')
    return value


def integer(value, name, ident, minimum=None):
    result = number(value, name, ident, minimum)
    if int(result) != result:
        raise ValueError(f'{ident} 字段 {name} 必须为整数')
    return int(result)


def plant_state(plant, period):
    """工厂在报告期内的状态。"""
    if plant is None:
        return '工厂待核'
    payload = plant['payload']
    if not payload.get('commissioned_on'):
        return '投用日期待核'
    if payload['commissioned_on'] > period['end']:
        return '待投用'
    if payload.get('closure_on') and payload['closure_on'] < period['start']:
        return '已关闭'
    return '已投用'


@dataclass
class Model:
    plants: dict = field(default_factory=dict)
    shipments: list = field(default_factory=list)
    certificates: list = field(default_factory=list)
    employment: list = field(default_factory=list)
    taxes: list = field(default_factory=list)
    revenues: list = field(default_factory=list)
    power: list = field(default_factory=list)
    service: list = field(default_factory=list)
    commitments: list = field(default_factory=list)
    investments: list = field(default_factory=list)
    fiscal: dict = field(default_factory=dict)
    invalid: list = field(default_factory=list)
    unsupported: list = field(default_factory=list)
    fallbacks: list = field(default_factory=list)
    used_documents: list = field(default_factory=list)


def _plant(document, ident):
    payload = document['payload']
    return {
        'country': text(payload.get('country'), 'country', ident),
        'name': payload.get('name'),
        'commissioned_on': day(payload['commissioned_on'], 'commissioned_on', ident) if payload.get('commissioned_on') else None,
        'closure_on': day(payload['closure_on'], 'closure_on', ident) if payload.get('closure_on') else None,
        'capacity_tons_per_year': number(payload['capacity_tons_per_year'], 'capacity_tons_per_year', ident, 0) if payload.get('capacity_tons_per_year') is not None else None,
    }


def _shipment(document, ident):
    payload = document['payload']
    result = {
        'lot': text(payload.get('lot') or document['id'], 'lot', ident),
        'origin': text(payload.get('origin'), 'origin', ident),
        'plant': text(payload.get('plant'), 'plant', ident),
        'tons': number(payload.get('tons'), 'tons', ident, 0),
        'processed_on': day(payload['processed_on'], 'processed_on', ident) if payload.get('processed_on') else None,
        'shipped_on': day(payload['shipped_on'], 'shipped_on', ident) if payload.get('shipped_on') else None,
        'destination': text(payload['destination'], 'destination', ident) if payload.get('destination') else None,
    }
    if result['tons'] <= 0:
        raise ValueError(f'{ident} 字段 tons 必须大于 0')
    return result


def _certificate(document, ident):
    payload = document['payload']
    return {
        'lot': text(payload.get('lot') or payload.get('shipment'), 'lot', ident),
        'origin_country': text(payload.get('origin_country') or payload.get('origin'), 'origin_country', ident),
        'issued_on': day(payload.get('issued_on'), 'issued_on', ident),
        'valid_until': day(payload['valid_until'], 'valid_until', ident) if payload.get('valid_until') else None,
        'evidence_ref': payload.get('evidence_ref'),
    }


def _period_fields(payload, ident):
    if payload.get('period_start') is not None or payload.get('period_end') is not None:
        if payload.get('period_start') is None or payload.get('period_end') is None:
            raise ValueError(f'{ident} 期间必须同时提供 period_start 与 period_end')
        start = day(payload['period_start'], 'period_start', ident)
        end = day(payload['period_end'], 'period_end', ident)
        if start > end:
            raise ValueError(f'{ident} 期间起始晚于结束')
        return {'period_start': start, 'period_end': end}
    if payload.get('fiscal_year') is not None and payload.get('jurisdiction') is not None:
        return {
            'fiscal_year': str(payload['fiscal_year']),
            'jurisdiction': text(payload['jurisdiction'], 'jurisdiction', ident),
        }
    raise ValueError(f'{ident} 缺少可核实期间（period_start/period_end 或 fiscal_year/jurisdiction）')


def _employment(document, ident):
    payload = document['payload']
    row = {
        'plant': text(payload.get('plant'), 'plant', ident),
        'local_employees': integer(payload.get('local_employees'), 'local_employees', ident, 0),
        'total_employees': integer(payload['total_employees'], 'total_employees', ident, 0) if payload.get('total_employees') is not None else None,
        'evidence_ref': payload.get('evidence_ref'),
    }
    row.update(_period_fields(payload, ident))
    return row


def _money(document, ident):
    payload = document['payload']
    row = {
        'plant': text(payload.get('plant'), 'plant', ident),
        'amount': number(payload.get('amount'), 'amount', ident, 0),
        'currency': text(payload.get('currency'), 'currency', ident).upper(),
        'category': text(payload['category'], 'category', ident) if payload.get('category') else '合计',
        'evidence_ref': payload.get('evidence_ref'),
    }
    row.update(_period_fields(payload, ident))
    return row


def _power(document, ident):
    payload = document['payload']
    row = {
        'plant': text(payload.get('plant'), 'plant', ident),
        'supplied_mwh': number(payload.get('supplied_mwh'), 'supplied_mwh', ident, 0),
        'coverage_ratio': number(payload['coverage_ratio'], 'coverage_ratio', ident, 0) if payload.get('coverage_ratio') is not None else None,
        'evidence_ref': payload.get('evidence_ref'),
    }
    if row['coverage_ratio'] is not None and row['coverage_ratio'] > 1:
        raise ValueError(f'{ident} 字段 coverage_ratio 必须在 0 到 1 之间')
    row.update(_period_fields(payload, ident))
    return row


def _service(document, ident):
    payload = document['payload']
    regions = payload.get('regions')
    if not isinstance(regions, list) or not regions:
        raise ValueError(f'{ident} 字段 regions 必须为非空列表')
    return {
        'plant': text(payload.get('plant'), 'plant', ident),
        'regions': [text(region, 'regions', ident) for region in regions],
        'effective_from': day(payload.get('effective_from'), 'effective_from', ident),
        'effective_to': day(payload['effective_to'], 'effective_to', ident) if payload.get('effective_to') else None,
        'evidence_ref': payload.get('evidence_ref'),
    }


def _commitment(document, ident):
    payload = document['payload']
    scope = text(payload.get('scope'), 'scope', ident)
    if scope not in SCOPES:
        raise ValueError(f'{ident} 字段 scope 必须为 {"/".join(SCOPES)} 之一')
    row = {
        'scope': scope,
        'target': number(payload.get('target'), 'target', ident, 0),
        'target_date': day(payload.get('target_date'), 'target_date', ident),
        'plant': text(payload['plant'], 'plant', ident) if payload.get('plant') else None,
        'country': text(payload['country'], 'country', ident) if payload.get('country') else None,
        'currency': text(payload['currency'], 'currency', ident).upper() if payload.get('currency') else None,
        'label': text(payload['label'], 'label', ident) if payload.get('label') else None,
        'announced_on': day(payload['announced_on'], 'announced_on', ident) if payload.get('announced_on') else None,
    }
    if row['plant'] is None and row['country'] is None:
        raise ValueError(f'{ident} 必须提供 plant 或 country')
    if scope in MONEY_SCOPES and not row['currency']:
        raise ValueError(f'{ident} 货币类承诺必须提供 currency')
    return row


def _fiscal(document, ident):
    payload = document['payload']
    start = day(payload.get('start'), 'start', ident)
    end = day(payload.get('end'), 'end', ident)
    if start > end:
        raise ValueError(f'{ident} 财年起始晚于结束')
    return {
        'jurisdiction': text(payload.get('jurisdiction'), 'jurisdiction', ident),
        'fiscal_year': str(payload.get('fiscal_year')),
        'start': start,
        'end': end,
    }


def normalize(documents):
    model = Model()
    handlers = {
        'plant': _plant,
        'shipment': _shipment,
        'origin_certificate': _certificate,
        'employment': _employment,
        'tax': _money,
        'revenue': _money,
        'power': _power,
        'service': _service,
        'commitment': _commitment,
        'fiscal': _fiscal,
        'investment': _money,
    }
    for document in documents:
        ident = f"{document['kind']}:{document['id']}"
        if document['kind'] not in handlers:
            model.unsupported.append({'document': ident, 'reason': '未知资料种类'})
            continue
        if not isinstance(document.get('payload'), dict):
            model.invalid.append({'document': ident, 'reason': '资料正文必须为对象'})
            continue
        try:
            record = handlers[document['kind']](document, ident)
        except ValueError as exc:
            model.invalid.append({'document': ident, 'reason': str(exc)})
            continue
        record = {**document, 'payload': record}
        if document['kind'] == 'plant':
            model.plants[document['id']] = record
        elif document['kind'] == 'shipment':
            model.shipments.append(record)
        elif document['kind'] == 'origin_certificate':
            model.certificates.append(record)
        elif document['kind'] == 'employment':
            model.employment.append(record)
        elif document['kind'] == 'tax':
            model.taxes.append(record)
        elif document['kind'] == 'revenue':
            model.revenues.append(record)
        elif document['kind'] == 'power':
            model.power.append(record)
        elif document['kind'] == 'service':
            model.service.append(record)
        elif document['kind'] == 'commitment':
            model.commitments.append(record)
        elif document['kind'] == 'investment':
            model.investments.append(record)
        elif document['kind'] == 'fiscal':
            model.fiscal[(record['payload']['jurisdiction'], record['payload']['fiscal_year'])] = record
    return model


def normalize_history(versions):
    """按身份取最新可解析版本；最新版本不合规时回退到上一可用版本并记录。

    更正资料不应让历史批次凭空消失：如果新 revision 校验失败，
    仍用最近一个合规版本参与核算，同时在 fallbacks 中说明被拒版本及原因。
    """
    grouped = {}
    for document in versions:
        grouped.setdefault((document['kind'], document['id']), []).append(document)
    chosen, fallbacks = [], []
    for key in sorted(grouped):
        from .evidence import parse_time
        chain = sorted(grouped[key], key=lambda row: (parse_time(row['received_at']), row['revision']))
        selected = None
        for candidate in reversed(chain):
            trial = normalize([candidate])
            if not trial.invalid:
                selected = candidate
                rejected = [row for row in chain if row is not candidate and parse_time(row['received_at']) > parse_time(candidate['received_at'])]
                for row in rejected:
                    failure = normalize([row]).invalid
                    fallbacks.append({
                        'document': f"{key[0]}:{key[1]}",
                        'revision': row['revision'],
                        'received_at': row['received_at'],
                        'used_revision': candidate['revision'],
                        'reason': failure[0]['reason'] if failure else '新版本未通过校验，沿用上一可用版本',
                    })
                break
        if selected is None:
            selected = chain[-1]
        chosen.append(selected)
    model = normalize(chosen)
    model.fallbacks = fallbacks
    model.used_documents = chosen
    return model


def load(db, known_at):
    """从证据库构建模型：最新可用版本 + 被拒版本回退说明。"""
    from .evidence import versions_all
    return normalize_history(versions_all(db, known_at))


def normalize_period(period):
    """把报告期间规范为日期字符串。"""
    if not isinstance(period, dict) or 'start' not in period or 'end' not in period:
        raise ValueError('必须提供期间 start 与 end')
    start = day(period['start'], 'start', 'report')
    end = day(period['end'], 'end', 'report')
    if start > end:
        raise ValueError('期间起始晚于结束')
    return {'start': start, 'end': end}


def resolve_period(payload, fiscal):
    """返回 (start, end) 或 (None, 原因)。只接受可完整落地的期间。"""
    if payload.get('period_start') and payload.get('period_end'):
        return (payload['period_start'], payload['period_end']), None
    if payload.get('fiscal_year') and payload.get('jurisdiction'):
        found = fiscal.get((payload['jurisdiction'], payload['fiscal_year']))
        if not found:
            return None, f"缺 {payload['jurisdiction']} 财年 {payload['fiscal_year']} 的起止口径"
        return (found['payload']['start'], found['payload']['end']), None
    return None, '未提供可核实期间'
