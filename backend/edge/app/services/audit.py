from backend.edge.app.models import AuditLog


def audit(db, actor: str, action: str, target: str | None = None, ip: str | None = None, **detail):
    """Nhật ký thao tác tại biên (FR10). Không commit: đi cùng giao dịch của thao tác."""
    db.add(AuditLog(actor=actor, action=action, target=target, ip=ip, detail=detail or None))
