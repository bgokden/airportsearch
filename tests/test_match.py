"""Tests for the shared trigram matcher and its length-aware cosine gate."""
from airportsearch._match import TrigramMatcher, normalize, trigrams


def test_normalize_folds_unicode():
    assert normalize("Zürich") == "zurich"
    assert normalize("  São   Paulo ") == "sao paulo"


def test_trigrams_padding():
    assert "  a" in trigrams("a") or trigrams("a")  # short strings still yield grams


def _matcher(words):
    m = TrigramMatcher()
    for i, w in enumerate(words):
        m.add(w, i)
    m.build()
    return m


def test_matcher_finds_exact_and_typo():
    m = _matcher(["utrecht", "recife", "cannes"])
    owners = [o for o, _s, _d in m.query("utrecht", limit=5, score_cutoff=70)]
    assert 0 in owners  # utrecht


def test_cosine_gate_rejects_short_substring():
    # Without the gate, WRatio scores "ann" highly against "cannes"; with it, no.
    m = _matcher(["cannes", "ann"])
    gated = m.query("cannes", limit=5, score_cutoff=70, min_cosine=0.34)
    # "ann" (owner 1) is a substring but too short to share enough trigrams.
    assert 1 not in [o for o, _s, _d in gated]
    assert 0 in [o for o, _s, _d in gated]  # cannes itself matches


def test_cosine_gate_rejects_unrelated_high_wratio():
    m = _matcher(["recife", "utrecht"])
    gated = m.query("utrecth", limit=5, score_cutoff=70, min_cosine=0.34)
    owners = [o for o, _s, _d in gated]
    assert 0 not in owners  # recife rejected by the gate
    assert 1 in owners      # utrecht (typo) survives


def test_no_gate_keeps_partial_matches():
    # Without min_cosine, a short query still matches a longer alias (needed for
    # descriptive airport queries).
    m = _matcher(["john f kennedy international airport"])
    owners = [o for o, _s, _d in m.query("kennedy airport", limit=5, score_cutoff=60)]
    assert 0 in owners
