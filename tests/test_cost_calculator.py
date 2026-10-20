"""
Tests for True Cost calculation and multi-currency FX conversions.
"""
import pytest
from backend.schemas.models import Opportunity, EvidenceField
from backend.agents.beacon import calculate_true_cost


def test_fully_funded_german_scholarship():
    opp = Opportunity(
        id="opp-daad",
        country="Germany",
        currency="EUR",
        tuition_yearly=EvidenceField(value="0", verified=True),
        living_cost_yearly=EvidenceField(value="11208", verified=True),
        funding_type=EvidenceField(value="Full Scholarship (Tuition + Monthly Stipend)", verified=True),
    )

    cost = calculate_true_cost(opp)
    assert cost.is_fully_funded is True
    assert cost.out_of_pocket_yearly_local == 0.0
    assert cost.out_of_pocket_yearly_inr == 0.0
    assert cost.exchange_rate_used == 93.0


def test_partial_funding_canadian_scholarship():
    opp = Opportunity(
        id="opp-uoft",
        country="Canada",
        currency="CAD",
        tuition_yearly=EvidenceField(value="25000", verified=True),
        living_cost_yearly=EvidenceField(value="20000", verified=True),
        funding_type=EvidenceField(value="Partial Tuition Waiver", verified=True),
    )

    cost = calculate_true_cost(opp)
    assert cost.is_fully_funded is False
    assert cost.out_of_pocket_yearly_local == 20000.0  # Living cost still out of pocket
    assert cost.exchange_rate_used == 62.5
    assert cost.out_of_pocket_yearly_inr == 20000.0 * 62.5
