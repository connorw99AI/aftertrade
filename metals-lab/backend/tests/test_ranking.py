from metals_lab.ranking import bot_stats, rank


def test_bot_stats():
    s = bot_stats([10, -5, 20, -15, 5], 300.0)
    assert s["closed_trades"] == 5 and s["net"] == 15 and s["win_rate_pct"] == 60.0
    assert s["profit_factor"] == round(35 / 20, 2)
    assert s["max_drawdown_pct"] == round((310 - 325) / 325 * 100, 2)


def test_rank_requires_trades_and_profit_then_smallest_drawdown():
    steady = [2, -1] * 30                      # 60 trades, +30, shallow drawdown
    swingy = [20, -18] * 30                    # 60 trades, +60, deeper drawdown
    losing = [1, -2] * 30
    few = [50] * 10
    rows = rank({"A": {"nets": swingy, "allocation": 300.0}, "B": {"nets": steady, "allocation": 300.0},
                 "C": {"nets": losing, "allocation": 300.0}, "D": {"nets": few, "allocation": 300.0}}, min_trades=50)
    assert [r["bot_id"] for r in rows if r["eligible"]] == ["B", "A"]
    assert rows[0]["rank"] == 1 and rows[1]["rank"] == 2
    by = {r["bot_id"]: r for r in rows}
    assert "not profitable after costs" in by["C"]["not_eligible_because"] and by["C"]["rank"] is None
    assert by["D"]["not_eligible_because"] == ["10/50 trades"]
