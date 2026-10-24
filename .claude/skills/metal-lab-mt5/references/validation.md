# Validating a bot before it trades real money

Contents: what a backtest can and cannot show, tester settings, overfitting, out-of-sample and walk-forward, costs, sample size, comparing live to tests.

## What a backtest shows

That the rules, applied to past prices with the modelled costs, would have produced those results. It does not show the rules will work next month. A good result is weak evidence; a bad result is fairly strong evidence. Use tests to reject bots, not to approve them.

## Strategy Tester settings that matter

- **Modelling:** "Every tick based on real ticks" where your broker provides history. Plain "every tick" synthesises ticks and flatters tight stops and scalping.
- **Spreads:** use variable (current or historical), not a fixed small number. Test with spreads 50% wider than normal as a stress case.
- **Commission and swap:** include the broker's actual values, not zero.
- **Deposit and leverage:** match the account you will trade, including leverage and lot limits, so the margin check and minimum-lot effects are real.
- **Server time:** the tester uses the history's server time; check session filters behave as intended.
- **Delay:** add execution delay (the tester's latency options) to see how fragile the edge is to a few hundred milliseconds.

## Overfitting

Signs: results fall apart when any parameter moves slightly; a very narrow optimum; dozens of parameters; a smooth equity curve with few trades; good in one regime (for example a long gold uptrend) and flat or negative elsewhere. Remedies: fewer parameters, parameter ranges that show a broad plateau of acceptable results rather than one peak, and testing across different regimes (trending, ranging, high and low volatility).

## Out-of-sample and walk-forward

- Hold out the most recent chunk of history. Optimise on the rest, then test once on the holdout. If you peek and re-optimise, it is no longer out-of-sample.
- Use MT5's forward test option in optimisation or run walk-forward manually: optimise on a window, trade the next window, roll forward, and judge the stitched out-of-sample result.
- Prefer results that are positive across most windows, not one great window carrying the total.

## Sample size

Few trades mean wide uncertainty. As a rule of thumb (opinion, not a theorem), do not trust a bot's statistics below about 100 independent trades, and be careful about scaling up before then. `review.py` already withholds KEEP below its minimum trade count and reports a confidence interval on expectancy; a wide interval that includes zero means "unproven".

Resampling past trades (bootstrapping) can size the uncertainty around the average, but it cannot create information the data does not hold. Do not treat thousands of resampled paths as thousands of independent tests.

## Costs eat thin edges

For metals scalpers especially, spread plus commission plus slippage can exceed the gross edge per trade. Check the cost share in `review.py`. If costs take a large fraction of gross profit, a small widening of spreads can flip the bot negative.

## Comparing live to the tests

Record each bot's tested profit factor and win rate in `metal_lab.json` (`expected_pf`, `expected_win_rate`). `review.py` flags live results that drift far from them. Drift is a prompt to investigate (spreads, regime, a code or setting change), not automatically proof the bot is broken, and not proof it is fine when it matches.
