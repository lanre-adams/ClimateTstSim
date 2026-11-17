import numpy as np
import pandas as pd
import pytest

from tstsim.mcda import (
    OBJ_KEYS, acceptability, analyse, apply_limits, dominates, explain, goodness, norm_weights, pareto_mask,
)


def _table():
    # deaths, anaemic %, doses, labour, resistance  (all minimised)
    rows = {
        "No treatment": [10.0, 3.8, 0.0, 0.0, 0.02],
        "Whole flock every 60 days": [4.2, 1.6, 6.0, 12.0, 1.00],
        "FAMACHA only": [2.0, 1.9, 0.7, 27.0, 0.76],
        "Bad": [11.0, 4.0, 6.5, 30.0, 1.00],  # dominated by whole flock
    }
    df = pd.DataFrame(rows, index=OBJ_KEYS).T.reset_index().rename(columns={"index": "policy"})
    return df


def test_goodness_ranges_and_direction():
    df = _table()
    for m in ("minmax", "reference", "target", "rank"):
        G = goodness(df, m)
        assert G.shape == (4, 5) and (G >= 0).all() and (G <= 1).all()
    G = goodness(df, "minmax")
    # FAMACHA has the fewest deaths -> best (1.0) on deaths
    assert G[2, 0] == pytest.approx(1.0) and G[3, 0] == pytest.approx(0.0)
    # reference policy scores 0.5 on every outcome with a non-zero reference value
    R = goodness(df, "reference")
    assert np.allclose(R[1], 0.5)


def test_dominance_and_pareto():
    df = _table()
    X = df[OBJ_KEYS].values
    assert dominates(X[1], X[3]) and not dominates(X[3], X[1])
    assert pareto_mask(df).tolist() == [True, True, True, False]


def test_weights_and_acceptability_sum_to_one():
    assert norm_weights([1, 1, 2, 0, 0]).sum() == pytest.approx(1)
    with pytest.raises(ValueError):
        norm_weights([0, 0, 0, 0, 0])
    acc = acceptability(goodness(_table()), samples=500, seed=1)
    assert acc.sum() == pytest.approx(1) and acc[3] == 0  # a dominated option never ranks first


def test_explanation_flip_threshold_reverses_ranking():
    df = _table()
    G = goodness(df, "minmax")
    w = norm_weights(np.ones(5))
    e = explain(G, w, 2, 0)  # FAMACHA vs No treatment
    assert e["margin"] >= 0 and e["against"]
    a, b = e["winner"], e["loser"]
    for c in e["against"]:
        k = [x for x in range(5) if c["objective"] == ["Deaths", "Anaemic goat-days", "Drug doses", "Labour", "Drug resistance"][x]][0]
        share = c["flip_weight_share"] + 0.01
        w2 = (1 - share) * np.delete(w, k) / np.delete(w, k).sum()
        w2 = np.insert(w2, k, share)
        assert G[b] @ w2 > G[a] @ w2  # just past the threshold, the other option wins


def test_limits_remove_harmful_cheap_option():
    df = _table()
    kept, excl = apply_limits(df, {"deaths_per_100_goat_years": 4.2})
    assert "No treatment" not in list(kept.policy) and excl[0]["policy"] == "No treatment"
    res = analyse(df, limits={"deaths_per_100_goat_years": 4.2})
    assert "No treatment" not in res["ranking"] and res["excluded"]
    assert res["all_policies"][0] == "No treatment"


def test_analyse_structure():
    res = analyse(_table(), method="target", weights=[2, 1, 1, 1, 1])
    assert set(res["by_method"]) == {"minmax", "reference", "target", "rank"}
    assert len(res["ranking"]) == 4 and res["explanation"]["text"]
    assert set(res["dominated_by"]["Bad"]) == {"No treatment", "Whole flock every 60 days", "FAMACHA only"}
