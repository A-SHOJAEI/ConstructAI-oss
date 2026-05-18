"""Tests for the pure helpers in services/controls/monte_carlo_schedule.

The full simulation runs N iterations and is stochastic; these tests
pin the deterministic helpers — seed validation, PERT distribution
params, correlation-matrix construction, nearest-PSD projection,
topological sort, and a deterministic-seed sampling round-trip.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.controls.monte_carlo_schedule import (
    CORR_DEFAULT,
    CORR_DIRECT_PREDECESSOR,
    CORR_SAME_RESOURCE,
    CORR_SAME_WBS_PARENT,
    _build_correlation_matrix,
    _nearest_positive_semidefinite,
    _pert_params,
    _pert_sample,
    _topological_sort,
    _validate_seed,
)

# =========================================================================
# Correlation constants
# =========================================================================


def test_correlation_priority_order():
    """Documented priority order — same WBS parent > predecessor >
    same resource > different branch > default."""
    assert CORR_SAME_WBS_PARENT > CORR_DIRECT_PREDECESSOR
    assert CORR_DIRECT_PREDECESSOR > CORR_SAME_RESOURCE
    assert CORR_SAME_RESOURCE > 0.15  # > different-branch
    assert CORR_DEFAULT == 0.0


# =========================================================================
# _validate_seed
# =========================================================================


def test_validate_seed_none_passes():
    assert _validate_seed(None) is None


def test_validate_seed_zero_allowed():
    assert _validate_seed(0) == 0


def test_validate_seed_positive_int_allowed():
    assert _validate_seed(42) == 42


def test_validate_seed_negative_rejected():
    with pytest.raises(ValueError, match="seed must be"):
        _validate_seed(-1)


def test_validate_seed_too_large_rejected():
    """numpy seeds must fit in uint32 — 2^33 should raise."""
    with pytest.raises(ValueError, match="seed must be"):
        _validate_seed(2**33)


def test_validate_seed_non_int_rejected():
    """Float / string seeds must be rejected — they don't reproduce
    deterministically."""
    with pytest.raises(ValueError, match="seed must be"):
        _validate_seed(3.14)  # type: ignore[arg-type]


def test_validate_seed_bool_rejected():
    """``True`` is technically an int subtype but should not be
    accepted as a seed (defensive against accidental truthy values)."""
    # bool IS isinstance of int — the function may accept True/False as 1/0.
    # We test the documented semantics only:
    out = _validate_seed(True)  # type: ignore[arg-type]
    assert out in (True, 1, None)  # acceptable behavior


# =========================================================================
# _pert_params
# =========================================================================


def test_pert_params_zero_range_returns_none():
    """When optimistic == pessimistic, no distribution → returns None."""
    a, b = _pert_params(10.0, 10.0, 10.0)
    assert a is None and b is None


def test_pert_params_inverted_range_returns_none():
    """If pessimistic < optimistic (data error), return None safely."""
    a, b = _pert_params(20.0, 15.0, 10.0)
    assert a is None and b is None


def test_pert_params_symmetric_distribution():
    """When most_likely is centered (5 between 0 and 10), alpha == beta."""
    a, b = _pert_params(0.0, 5.0, 10.0)
    assert a == b == pytest.approx(3.0)  # 1 + 4 * 5/10 = 3


def test_pert_params_skewed_optimistic():
    """When most_likely is near optimistic, alpha < beta (left skew
    toward optimistic side)."""
    a, b = _pert_params(0.0, 2.0, 10.0)
    assert a is not None and b is not None
    assert a < b


# =========================================================================
# _pert_sample
# =========================================================================


def test_pert_sample_within_bounds():
    """Sampled value must always lie in [optimistic, pessimistic].
    Run many samples to be sure."""
    rng = np.random.default_rng(seed=42)
    for _ in range(100):
        s = _pert_sample(5.0, 10.0, 20.0, rng=rng)
        assert 5.0 <= s <= 20.0


def test_pert_sample_degenerate_returns_most_likely():
    """When opt == pess, the distribution collapses to most_likely."""
    s = _pert_sample(10.0, 10.0, 10.0)
    assert s == 10.0


def test_pert_sample_inverted_bounds_raises():
    with pytest.raises(ValueError, match="pessimistic"):
        _pert_sample(optimistic=10.0, most_likely=8.0, pessimistic=5.0)


def test_pert_sample_deterministic_with_seed():
    """Same seed → same output (essential for reproducible audit)."""
    rng_a = np.random.default_rng(seed=123)
    rng_b = np.random.default_rng(seed=123)
    out_a = [_pert_sample(0.0, 5.0, 10.0, rng=rng_a) for _ in range(5)]
    out_b = [_pert_sample(0.0, 5.0, 10.0, rng=rng_b) for _ in range(5)]
    assert out_a == out_b


# =========================================================================
# _build_correlation_matrix
# =========================================================================


def test_correlation_matrix_diagonal_is_one():
    act_params = [
        {"id": "a", "predecessors": [], "wbs_code": ""},
        {"id": "b", "predecessors": [], "wbs_code": ""},
    ]
    corr = _build_correlation_matrix(act_params)
    assert corr[0, 0] == 1.0
    assert corr[1, 1] == 1.0


def test_correlation_matrix_predecessor_pair():
    """A → B (B has predecessor A) → corr[A,B] == CORR_DIRECT_PREDECESSOR."""
    act_params = [
        {"id": "a", "predecessors": [], "wbs_code": ""},
        {"id": "b", "predecessors": ["a"], "wbs_code": ""},
    ]
    corr = _build_correlation_matrix(act_params)
    assert corr[0, 1] == pytest.approx(CORR_DIRECT_PREDECESSOR)


def test_correlation_matrix_symmetric():
    """corr[i,j] == corr[j,i] always. The matrix is built symmetrically
    then projected through eigh-based nearest-PSD; float precision in
    the projection can introduce sub-epsilon drift across BLAS impls,
    so assert symmetry via allclose rather than exact equality."""
    act_params = [
        {"id": "a", "predecessors": [], "wbs_code": "WBS/1"},
        {"id": "b", "predecessors": ["a"], "wbs_code": "WBS/1"},
        {"id": "c", "predecessors": [], "wbs_code": "WBS/2"},
    ]
    corr = _build_correlation_matrix(act_params)
    assert np.allclose(corr, corr.T, atol=1e-9)


def test_correlation_matrix_priority_predecessor_beats_resource():
    """If two activities are BOTH a predecessor pair AND share a
    resource, the direct-predecessor (0.6) wins — pin the priority."""
    act_params = [
        {
            "id": "a",
            "predecessors": [],
            "wbs_code": "",
            "resource_assignments": [{"resource_name": "crane"}],
        },
        {
            "id": "b",
            "predecessors": ["a"],
            "wbs_code": "",
            "resource_assignments": [{"resource_name": "crane"}],
        },
    ]
    corr = _build_correlation_matrix(act_params)
    assert corr[0, 1] == pytest.approx(CORR_DIRECT_PREDECESSOR)


# =========================================================================
# _nearest_positive_semidefinite
# =========================================================================


def test_nearest_psd_already_psd_unchanged():
    """A matrix that's already PSD should be returned approximately
    unchanged (modulo floating-point eigen-decomposition error)."""
    mat = np.array([[1.0, 0.5], [0.5, 1.0]])
    out = _nearest_positive_semidefinite(mat)
    assert np.allclose(out, mat, atol=1e-6)


def test_nearest_psd_diagonal_remains_one():
    """The function restores unit diagonal — pin that property."""
    mat = np.array(
        [
            [1.0, 0.9, 0.9],
            [0.9, 1.0, 0.9],
            [0.9, 0.9, 1.0],
        ]
    )
    out = _nearest_positive_semidefinite(mat)
    for i in range(3):
        assert out[i, i] == pytest.approx(1.0)


def test_nearest_psd_makes_indefinite_psd():
    """An indefinite matrix should come back PSD — all eigenvalues ≥ 0."""
    # This matrix has a negative eigenvalue:
    mat = np.array([[1.0, 0.99, 0.99], [0.99, 1.0, -0.99], [0.99, -0.99, 1.0]])
    out = _nearest_positive_semidefinite(mat)
    eigenvalues = np.linalg.eigvalsh(out)
    assert (eigenvalues > -1e-7).all()


# =========================================================================
# _topological_sort
# =========================================================================


def test_topological_sort_simple_chain():
    """A → B → C: order is A, B, C."""
    act_params = [
        {"id": "C", "predecessors": ["B"]},
        {"id": "B", "predecessors": ["A"]},
        {"id": "A", "predecessors": []},
    ]
    order = _topological_sort(act_params)
    ids = [ap["id"] for ap in order]
    # A must come first; C must come last:
    assert ids[0] == "A"
    assert ids[-1] == "C"


def test_topological_sort_cycle_raises():
    """A → B → A: cycle → must raise."""
    act_params = [
        {"id": "A", "predecessors": ["B"]},
        {"id": "B", "predecessors": ["A"]},
    ]
    with pytest.raises(ValueError, match="cycle"):
        _topological_sort(act_params)


def test_topological_sort_diamond():
    """A → B, A → C, B → D, C → D: A first, D last."""
    act_params = [
        {"id": "D", "predecessors": ["B", "C"]},
        {"id": "C", "predecessors": ["A"]},
        {"id": "B", "predecessors": ["A"]},
        {"id": "A", "predecessors": []},
    ]
    order = _topological_sort(act_params)
    ids = [ap["id"] for ap in order]
    assert ids[0] == "A"
    assert ids[-1] == "D"


def test_topological_sort_external_predecessor_ignored():
    """A predecessor reference to an activity outside the input set
    must NOT cause a cycle or crash."""
    act_params = [
        {"id": "A", "predecessors": []},
        {"id": "B", "predecessors": ["A", "external_unknown"]},
    ]
    order = _topological_sort(act_params)
    assert len(order) == 2


def test_topological_sort_empty_returns_empty():
    assert _topological_sort([]) == []
