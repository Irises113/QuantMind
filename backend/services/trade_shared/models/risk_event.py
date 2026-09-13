"""Risk trigger event (audit log for scan-and-act rules)."""

from sqlalchemy import JSON, Column, Date, DateTime, Float, Index, Integer, String
from sqlalchemy.sql import func

from .base import Base


class RiskEvent(Base):
    """One row per trigger attempt (filled / skipped / alert-only / failed)."""

    __tablename__ = "risk_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, nullable=True, index=True)
    rule_type = Column(String(50), nullable=False, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)
    user_id = Column(Integer, nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    symbol = Column(String(32), nullable=False, default="*")
    action = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, index=True)
    trigger_price = Column(Float, nullable=True)
    cost_price = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    quantity = Column(Float, nullable=True)
    order_ids = Column(JSON, nullable=True)
    message = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now(), server_default=func.now())

    __table_args__ = (
        Index("idx_risk_events_user_date", "tenant_id", "user_id", "trade_date"),
        Index("idx_risk_events_rule_date", "rule_id", "trade_date"),
    )

    def __repr__(self) -> str:
        return (
            f"<RiskEvent(id={self.id}, rule_id={self.rule_id}, "
            f"type={self.rule_type}, symbol={self.symbol}, status={self.status})>"
        )
