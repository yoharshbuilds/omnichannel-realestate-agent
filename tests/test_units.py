from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.emailparse import normalize_email
from app.intake import normalize, normalize_phone
from app.pricing import availability, availability_text, cost_sheet, inr, inr_spoken, parse_budget
from app.scoring import score

IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.parametrize("spoken, expected", [
    ("harsh dot shah at gmail dot com", "harsh.shah@gmail.com"),
    ("Harsh.Shah@Gmail.com", "harsh.shah@gmail.com"),
    ("h a r s h underscore nine two at the rate yahoo dot co dot in", "harsh_92@yahoo.co.in"),
    ("harsh at gmail", None),
    ("", None),
])
def test_normalize_email(spoken, expected):
    assert normalize_email(spoken) == expected


@pytest.mark.parametrize("n, grouped, spoken", [
    (999, "Rs. 999", "0 lakh"),
    (9516000, "Rs. 95,16,000", "95.2 lakh"),
    (11234567, "Rs. 1,12,34,567", "1.12 crore"),
    (165000000, "Rs. 16,50,00,000", "16.5 crore"),
])
def test_inr(n, grouped, spoken):
    assert inr(n) == grouped
    if n > 1000:
        assert inr_spoken(n) == spoken


@pytest.mark.parametrize("text, rupees", [
    ("1.2 crore", 12_000_000), ("80 lakh", 8_000_000), ("70-80 L", 8_000_000), ("1 cr", 10_000_000),
    ("95,00,000", 9_500_000), ("90L", 9_000_000), ("around 1.5 Cr", 15_000_000), ("no idea", None), (None, None),
])
def test_parse_budget(text, rupees):
    assert parse_budget(text) == rupees


def test_cost_sheet_adds_up():
    s = cost_sheet("skyline-heights", "3 BHK", 12)
    lines = dict(s["lines"])
    assert lines["Floor rise (floor 12)"] == 8 * 40 * 1240  # floors 5..12 carry the rise
    assert s["agreement_value"] == 1240 * 13300 + 8 * 40 * 1240 + 400000
    assert s["total"] == pytest.approx(19_365_568)
    assert dict(cost_sheet("palm-grove", "3 bhk villa")["lines"])["GST 0%"] == 0  # ready to move


def test_cost_sheet_clamps_floor_and_rejects_unknown_config():
    assert cost_sheet("metro-one", "2 BHK", 99)["floor"] == 18
    assert cost_sheet("metro-one", "4 BHK") is None


def test_over_budget_suggests_best_alternative():
    text = availability_text(availability("palm grove", "3 bhk", "1.2 crore"))
    assert "above their budget" in text and "Skyline Heights 2 BHK" in text


def test_scoring():
    hot = {"budget": "2 crore", "bhk": "3 BHK", "timeline": "2 months", "project_id": "skyline-heights",
           "decision_maker": "yes", "loan_status": "pre-approved", "stage": "visit_booked"}
    cold = {"timeline": "just exploring"}
    assert score(hot)[1] == "hot"
    assert score(cold)[1] == "cold"


@pytest.mark.parametrize("raw, phone", [
    ("9800000001", "+919800000001"), ("+91 98000 00001", "+919800000001"), ("09800000001", "+919800000001"),
    ("+1 415 555 0100", "+14155550100"), ("12345", None), ("", None),
])
def test_normalize_phone(raw, phone):
    assert normalize_phone(raw) == phone


def test_intake_meta_lead_ads_shape():
    lead = normalize({
        "campaign_name": "Skyline launch",
        "field_data": [{"name": "full_name", "values": ["Harsh Shah"]},
                       {"name": "phone_number", "values": ["+919800000001"]},
                       {"name": "project_name", "values": ["skyline"]}],
    })
    assert lead["name"] == "Harsh Shah" and lead["phone"] == "+919800000001"
    assert lead["project_id"] == "skyline-heights" and lead["source"] == "meta_lead_ads"
    assert lead["campaign"] == "Skyline launch"


def test_intake_portal_shape():
    lead = normalize({"Mobile": "9800000002", "PropertyName": "Metro One", "source": "99acres"})
    assert lead["phone"] == "+919800000002" and lead["project_id"] == "metro-one" and lead["source"] == "99acres"


def test_calling_window():
    from app.scheduler import next_call_slot
    early = datetime(2030, 3, 6, 8, 0, tzinfo=IST)
    late = datetime(2030, 3, 6, 20, 0, tzinfo=IST)
    inside = datetime(2030, 3, 6, 12, 0, tzinfo=IST)
    assert next_call_slot(early).astimezone(IST).hour == 10
    assert next_call_slot(late).astimezone(IST).day == 7 and next_call_slot(late).astimezone(IST).hour == 10
    assert next_call_slot(inside) == inside
