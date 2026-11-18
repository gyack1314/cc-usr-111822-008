"""货币折算系数与量纲分离。

不同币种和口径的金额不能直接相加。这里把每种金额落到它自己的口径：
tax 单独合计、revenue 单独合计、investment 单独合计，互不相加；
每个系数给出 low / base 两档，用于形成可核实的贡献区间。
未配置的币种不猜汇率，保留原币明细并标记为无法合计。
"""
from decimal import Decimal

PLACEHOLDER_FACTORS = {
    ('tax', 'CNY', 'tax'): {'low': 0.90, 'base': 0.95},
    ('tax', 'JPY', 'tax'): {'low': 0.044, 'base': 0.048},
    ('tax', 'USD', 'tax'): {'low': 0.94, 'base': 1.00},
    ('tax', 'MYR', 'tax'): {'low': 0.20, 'base': 0.22},
    ('tax', 'IDR', 'tax'): {'low': 0.000060, 'base': 0.000065},
    ('revenue', 'CNY', 'revenue'): {'low': 0.88, 'base': 0.93},
    ('revenue', 'JPY', 'revenue'): {'low': 0.043, 'base': 0.047},
    ('revenue', 'USD', 'revenue'): {'low': 0.93, 'base': 0.98},
    ('revenue', 'MYR', 'revenue'): {'low': 0.19, 'base': 0.21},
    ('revenue', 'IDR', 'revenue'): {'low': 0.000058, 'base': 0.000062},
    ('investment', 'CNY', 'investment'): {'low': 0.88, 'base': 0.93},
    ('investment', 'JPY', 'investment'): {'low': 0.043, 'base': 0.047},
    ('investment', 'USD', 'investment'): {'low': 0.93, 'base': 0.98},
    ('investment', 'MYR', 'investment'): {'low': 0.19, 'base': 0.21},
    ('investment', 'IDR', 'investment'): {'low': 0.000058, 'base': 0.000062},
}

PLACEHOLDER_SOURCE = '示例折算系数表（占位，部署时须替换为审计口径）'


class Factors:
    def __init__(self, table=None, source=None):
        if table is None:
            self.table = dict(PLACEHOLDER_FACTORS)
            self.source = source or PLACEHOLDER_SOURCE
            self.configured = False
        else:
            self.table = {}
            for key, value in table.items():
                scope, currency, target = _split_key(key)
                self.table[(scope, currency, target)] = {
                    'low': float(value['low']),
                    'base': float(value['base']),
                }
            self.source = source or '自定义折算系数表'
            self.configured = True

    def target(self, scope):
        return scope

    def get(self, scope, currency):
        currency = currency.upper()
        return self.table.get((scope, currency, self.target(scope))), self.target(scope)

    def convert(self, scope, amount, currency):
        factor, target = self.get(scope, currency)
        if factor is None:
            return None, target, '未配置 ' + currency + '→' + target + ' 折算系数'
        return {
            'low': float(Decimal(str(amount)) * Decimal(str(factor['low']))),
            'base': float(Decimal(str(amount)) * Decimal(str(factor['base']))),
        }, target, None


def _split_key(key):
    if isinstance(key, (tuple, list)):
        scope, currency, target = key
    else:
        parts = str(key).split(':')
        if len(parts) != 3:
            raise ValueError('折算系数键必须为 口径:币种:目标口径: ' + str(key))
        scope, currency, target = parts
    scope, currency, target = scope.strip().lower(), currency.strip().upper(), target.strip().lower()
    if scope not in ('tax', 'revenue', 'investment'):
        raise ValueError('未知折算口径: ' + scope)
    if target != scope:
        raise ValueError('折算目标口径必须与来源口径一致: ' + str(key))
    return scope, currency, target


def from_json(payload):
    """从输入资料读取折算系数；没有提供时使用占位表并在报告中注明。"""
    if payload is None:
        return Factors()
    if not isinstance(payload, dict) or not isinstance(payload.get('rates'), dict):
        raise ValueError('factors 必须包含 rates 对象')
    return Factors(payload['rates'], payload.get('source'))
