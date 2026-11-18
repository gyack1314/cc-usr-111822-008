"""读取业务样例，出具不可变合作报告并展示地区贡献与未兑现承诺。"""
import argparse
import json
from pathlib import Path

from .contribution import contribution
from .evidence import connect, receive
from .factories import production
from .factors import from_json
from .models import load
from .reports import history, issue, read


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description='跨境电池合作贡献核算')
    parser.add_argument('input', help='业务资料 JSON')
    parser.add_argument('--report', help='报告名称；提供时保存不可变版本')
    parser.add_argument('--revision', type=int, help='查看已出具报告的指定版本')
    parser.add_argument('--history', action='store_true', help='列出同名报告的全部版本与变化')
    parser.add_argument('--database', default=':memory:', help='SQLite 路径；默认内存库')
    args = parser.parse_args()

    data = _load(args.input)
    db = connect(args.database)
    try:
        for item in data['documents']:
            receive(db, item)
        factors = from_json(data.get('factors'))

        if args.history:
            print(json.dumps(history(db, args.report), ensure_ascii=False, indent=2))
            return
        if args.revision is not None:
            print(json.dumps(read(db, args.report, args.revision), ensure_ascii=False, indent=2))
            return
        if args.report:
            body = issue(db, args.report, data['known_at'], data['period'], factors)
            print(json.dumps(body, ensure_ascii=False, indent=2))
            return

        model = load(db, data['known_at'])
        result = contribution(model, data['period'], factors)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(production(db, data['known_at'], data['period']), ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == '__main__':
    main()
