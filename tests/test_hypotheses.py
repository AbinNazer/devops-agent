"""
Tests for app/control/hypotheses.py — structured hypothesis engine.

Verifies evidence-grounded confidence, hypothesis ranking, and status updates.
"""
import pytest

from app.control.hypotheses import (
    create_hypothesis, compute_confidence, update_hypothesis_status,
    add_evidence_for, add_evidence_against, rank_hypotheses, select_best,
    Hypothesis, HypothesisStatus,
)


# --- Hypothesis creation ---

def test_create_hypothesis_basic():
    """Basic hypothesis creation should work."""
    h = create_hypothesis("Redis is down")
    assert h.statement == "Redis is down"
    assert h.id.startswith("hyp-")
    assert h.status in (s.value for s in HypothesisStatus)


def test_create_hypothesis_with_evidence():
    """Hypothesis with evidence should compute confidence."""
    h = create_hypothesis(
        "Backend failing due to Redis",
        evidence_for=["Logs show connection timeout", "Redis container unhealthy"],
        evidence_against=["Redis is sometimes responsive"],
    )
    assert len(h.evidence_for) == 2
    assert len(h.evidence_against) == 1
    assert h.confidence > 0


def test_create_hypothesis_no_evidence():
    """Hypothesis with no evidence should have low confidence."""
    h = create_hypothesis("Something might be wrong")
    assert h.confidence <= 0.15  # Base confidence + no evidence


# --- Confidence computation ---

def test_confidence_increases_with_evidence_for():
    """Confidence should increase with supporting evidence."""
    h1 = create_hypothesis("Test", evidence_for=["evidence1"])
    h2 = create_hypothesis("Test", evidence_for=["evidence1", "evidence2", "evidence3"])
    assert h2.confidence > h1.confidence


def test_confidence_decreases_with_evidence_against():
    """Confidence should decrease with contradicting evidence."""
    h1 = create_hypothesis("Test", evidence_for=["evidence1"])
    h2 = create_hypothesis("Test", evidence_for=["evidence1"], evidence_against=["contradiction"])
    assert h2.confidence < h1.confidence


def test_confidence_clamped_to_0_1():
    """Confidence should never go below 0 or above 1."""
    h = create_hypothesis(
        "Test",
        evidence_for=["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8"],
        evidence_against=["c1", "c2", "c3", "c4", "c5"],
    )
    assert 0.0 <= h.confidence <= 1.0


def test_high_confidence_marked_as_supported():
    """Hypothesis with high confidence should be SUPPORTED."""
    h = create_hypothesis(
        "Test",
        evidence_for=["e1", "e2", "e3", "e4"],
    )
    assert h.confidence >= 0.7
    assert h.status == HypothesisStatus.SUPPORTED.value


def test_medium_confidence_marked_as_weak():
    """Hypothesis with medium confidence should be WEAK."""
    h = create_hypothesis("Test", evidence_for=["e1"])
    if 0.4 <= h.confidence < 0.7:
        assert h.status == HypothesisStatus.WEAK.value


# --- Adding evidence ---

def test_add_evidence_for():
    """Adding supporting evidence should increase confidence."""
    h = create_hypothesis("Test")
    initial_conf = h.confidence
    h = add_evidence_for(h, "new supporting evidence")
    assert h.confidence >= initial_conf
    assert "new supporting evidence" in h.evidence_for


def test_add_evidence_against():
    """Adding contradicting evidence should decrease confidence."""
    h = create_hypothesis("Test", evidence_for=["e1", "e2", "e3"])
    initial_conf = h.confidence
    h = add_evidence_against(h, "contradicting evidence")
    assert h.confidence < initial_conf
    assert "contradicting evidence" in h.evidence_against


def test_add_evidence_deduplicates():
    """Adding the same evidence twice should not duplicate."""
    h = create_hypothesis("Test")
    h = add_evidence_for(h, "same evidence")
    h = add_evidence_for(h, "same evidence")
    assert h.evidence_for.count("same evidence") == 1


def test_add_evidence_removes_from_opposite():
    """Adding evidence to 'for' should remove it from 'against' and vice versa."""
    h = create_hypothesis("Test", evidence_for=["e1"], evidence_against=["contradiction"])
    h = add_evidence_for(h, "contradiction")  # Move from against to for
    assert "contradiction" in h.evidence_for
    assert "contradiction" not in h.evidence_against


# --- Ranking ---

def test_rank_hypotheses_by_confidence():
    """Hypotheses should be ranked by confidence (highest first)."""
    h1 = create_hypothesis("Low", evidence_for=["e1"])
    h2 = create_hypothesis("High", evidence_for=["e1", "e2", "e3", "e4"])
    h3 = create_hypothesis("Medium", evidence_for=["e1", "e2"])

    ranked = rank_hypotheses([h1, h2, h3])
    assert ranked[0].confidence >= ranked[1].confidence >= ranked[2].confidence


def test_select_best():
    """select_best should return the highest-confidence hypothesis."""
    h1 = create_hypothesis("Low", evidence_for=["e1"])
    h2 = create_hypothesis("High", evidence_for=["e1", "e2", "e3", "e4"])

    best = select_best([h1, h2])
    assert best.id == h2.id


def test_select_best_with_no_hypotheses():
    """select_best with empty list should return None."""
    assert select_best([]) is None


# --- Status updates ---

def test_status_update_reflects_evidence():
    """Status should update when evidence changes."""
    h = create_hypothesis("Test")
    h = add_evidence_for(h, "strong evidence 1")
    h = add_evidence_for(h, "strong evidence 2")
    h = add_evidence_for(h, "strong evidence 3")
    h = add_evidence_for(h, "strong evidence 4")
    assert h.status == HypothesisStatus.SUPPORTED.value

    h = add_evidence_against(h, "contradiction 1")
    h = add_evidence_against(h, "contradiction 2")
    h = add_evidence_against(h, "contradiction 3")
    h = add_evidence_against(h, "contradiction 4")
    h = add_evidence_against(h, "contradiction 5")
    # Should now be REJECTED (more against than for)
    assert h.status == HypothesisStatus.REJECTED.value


# --- Serialization ---

def test_hypothesis_to_dict():
    """Hypothesis.to_dict should serialize properly."""
    h = create_hypothesis(
        "Redis is down",
        evidence_for=["Logs show errors"],
        related_components=["redis"],
    )
    d = h.to_dict()
    assert d["statement"] == "Redis is down"
    assert "Logs show errors" in d["evidence_for"]
    assert "redis" in d["related_components"]
    assert "confidence" in d
    assert "status" in d
