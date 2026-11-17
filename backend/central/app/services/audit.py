from backend.central.app.models import AuditLog


def audit(db, user, action: str, target: str | None = None, ip: str | None = None, **detail):
    """Ghi audit log cho thao tác quan trọng (FR10). Không commit: đi cùng giao dịch của thao tác."""
    db.add(AuditLog(user_id=getattr(user, "id", None), action=action, target=target, ip=ip,
                    detail=detail or None))
