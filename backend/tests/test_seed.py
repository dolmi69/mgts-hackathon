from __future__ import annotations

from sqlalchemy import select

from app.models import Intern, RiskFlag
from app.seed_data import seed_database


def test_seed_end_to_end(db):
    stats = seed_database(db, analyze=True, reset=True)
    assert stats["answers_imported"] > 100
    assert stats["pending_analysis"] == 0

    db.expire_all()
    flags = list(db.scalars(select(RiskFlag)).all())
    codes = {flag.rule_code for flag in flags}
    assert {"R1", "R2", "R3", "R4", "R5"}.issubset(codes)

    control = db.scalar(select(Intern).where(Intern.external_id == "int-007"))
    control_flags = list(
        db.scalars(select(RiskFlag).where(RiskFlag.intern_id == control.id)).all()
    )
    assert control_flags == []

    missing = db.scalar(select(Intern).where(Intern.external_id == "int-004"))
    missing_flags = list(
        db.scalars(select(RiskFlag).where(RiskFlag.intern_id == missing.id)).all()
    )
    assert "R4" in {flag.rule_code for flag in missing_flags}

    high = db.scalar(select(Intern).where(Intern.external_id == "int-001"))
    high_flags = list(
        db.scalars(select(RiskFlag).where(RiskFlag.intern_id == high.id)).all()
    )
    assert "R1" in {flag.rule_code for flag in high_flags}
