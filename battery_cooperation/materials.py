"""货运批次与原产地证明的读取入口。"""
from .evidence import at


def documents(db, known_at):
    return at(db, 'shipment', known_at)


def shipments(db, known_at):
    """保留旧接口：返回原始货运批次。"""
    return [{'lot': r['id'], **r['payload'], 'source': r['digest']} for r in at(db, 'shipment', known_at)]


def normalized(db, known_at):
    from .models import load
    return load(db, known_at)
