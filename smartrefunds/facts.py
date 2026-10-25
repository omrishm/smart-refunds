"""The fixed set of facts the AI note reader extracts. Rules read these; the AI never decides."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Tri = Literal["yes", "no", "not_mentioned"]


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Tri
    evidence: str = ""  # verbatim quote from the notes; "" when not mentioned


class PhotoFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Literal["present", "absent", "not_mentioned"]
    evidence: str = ""


class LocationFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str | None = None  # lowercase slug, e.g. "northfield"
    evidence: str = ""


class Facts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    photo_or_video_evidence: PhotoFact
    carrier_signature_or_photo: Fact
    carrier_investigation_open: Fact
    fraud_reported: Fact
    fraud_confirmed: Fact
    picking_error_confirmed: Fact
    shipped_item_matches_order: Fact
    admits_delivered: Fact
    admits_own_fault: Fact
    already_refunded_elsewhere: Fact
    reason_contradicts_note: Fact
    damage_cosmetic_only: Fact
    instructions_to_system: Fact
    repeat_claim_pattern: Fact
    location_tag: LocationFact
    summary: str
    language: str


# Facts with a value and an evidence quote (everything except location_tag / summary / language).
FACT_NAMES: tuple[str, ...] = tuple(
    n for n in Facts.model_fields if n not in ("location_tag", "summary", "language")
)
APPROVAL_EVIDENCE_FACTS = (
    "photo_or_video_evidence", "picking_error_confirmed", "fraud_confirmed", "fraud_reported", "location_tag",
)
# Plain-English labels for screens.
FACT_LABELS = {
    "photo_or_video_evidence": "Photo or video evidence",
    "carrier_signature_or_photo": "Carrier signature or delivery photo",
    "carrier_investigation_open": "Carrier investigation open",
    "fraud_reported": "Card reported stolen or unauthorised",
    "fraud_confirmed": "Fraud confirmed by fraud team or bank",
    "picking_error_confirmed": "Picking error confirmed",
    "shipped_item_matches_order": "Shipped item matches the order",
    "admits_delivered": "Customer says the parcel did arrive",
    "admits_own_fault": "Customer caused the damage",
    "already_refunded_elsewhere": "Already refunded elsewhere",
    "reason_contradicts_note": "Notes contradict the stated reason",
    "damage_cosmetic_only": "Cosmetic damage only",
    "instructions_to_system": "Instructions addressed to the refund system",
    "repeat_claim_pattern": "Pattern of repeat claims",
    "location_tag": "Location",
}
ESCALATION_FACTS = (
    "carrier_signature_or_photo", "admits_delivered", "admits_own_fault", "already_refunded_elsewhere",
    "reason_contradicts_note", "damage_cosmetic_only", "instructions_to_system", "repeat_claim_pattern",
    "shipped_item_matches_order", "fraud_reported", "fraud_confirmed",
)
