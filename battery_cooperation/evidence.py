"""保存外部业务资料的已知时间和不可覆盖的版本。"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone


def parse_time(value, end_of_day=False):
    """解析 ISO 时间；无时区按 UTC，仅有日期的期间结束按当日末处理。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('时间必须为 ISO 字符串')
    text = value.strip()
    date_only = len(text) == 10 and text[4] == '-' and text[7] == '-'
    try:
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('时间格式无效: ' + value) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if date_only and end_of_day:
        moment = moment.replace(hour=23, minute=59, second=59, microsecond=999999)
    return moment


def connect(path=':memory:'):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS documents (kind TEXT, identity TEXT, revision TEXT, received_at TEXT, body TEXT, digest TEXT, PRIMARY KEY(kind,identity,revision))')
    return db


def receive(db, document):
    for key in ('kind', 'id', 'revision', 'received_at', 'payload'):
        if key not in document:
            raise ValueError('资料缺少字段: ' + key)
    parse_time(document['received_at'])
    if not isinstance(document['payload'], dict):
        raise ValueError('资料正文必须为对象')
    body = json.dumps(document['payload'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(body.encode()).hexdigest()
    key = (document['kind'], document['id'], document['revision'])
    found = db.execute('SELECT digest FROM documents WHERE kind=? AND identity=? AND revision=?', key).fetchone()
    if found and found[0] != digest:
        raise ValueError('同一资料版本内容冲突')
    with db:
        db.execute('INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?)', (*key, document['received_at'], body, digest))
    return digest


def at(db, kind, known_at):
    """返回截至 known_at 每个资料身份的最新版本。"""
    cutoff = parse_time(known_at)
    latest = {}
    rows = db.execute(
        'SELECT identity,revision,received_at,body,digest FROM documents WHERE kind=? ORDER BY identity,received_at,revision',
        (kind,),
    ).fetchall()
    for identity, revision, received, body, digest in rows:
        received_at = parse_time(received)
        if received_at > cutoff:
            continue
        marker = (received_at, revision)
        if identity not in latest or marker > latest[identity][0]:
            latest[identity] = (marker, {
                'kind': kind,
                'id': identity,
                'revision': revision,
                'received_at': received,
                'payload': json.loads(body),
                'digest': digest,
            })
    return [value[1] for _, value in sorted(latest.items())]


def all_kinds(db):
    return [row[0] for row in db.execute('SELECT DISTINCT kind FROM documents ORDER BY kind')]


def versions_all(db, known_at):
    """取截至 known_at 的全部资料版本（同身份各 revision 都在），供上层回退校验。"""
    cutoff = parse_time(known_at)
    documents = []
    for kind in all_kinds(db):
        for identity, revision, received, body, digest in db.execute(
            'SELECT identity,revision,received_at,body,digest FROM documents WHERE kind=?', (kind,)
        ):
            if parse_time(received) > cutoff:
                continue
            documents.append({
                'kind': kind,
                'id': identity,
                'revision': revision,
                'received_at': received,
                'payload': json.loads(body),
                'digest': digest,
            })
    return sorted(documents, key=lambda row: (row['kind'], row['id'], parse_time(row['received_at']), row['revision']))


def snapshot(db, kinds, known_at):
    """按种类汇总截至 known_at 的资料快照，供报告固定输入。"""
    documents = []
    for kind in sorted(kinds):
        documents.extend(at(db, kind, known_at))
    return sorted(documents, key=lambda row: (row['kind'], row['id'], row['revision']))
