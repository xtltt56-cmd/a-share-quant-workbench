# Stage 3B Fast Research Report

- Data mode: `fixture`
- Source commit: `fc2ae89e434b7131049bb2b3423932766699356d`
- VectorBT: `not-installed`

> TEST / FIXTURE DATA - NOT INVESTMENT EVIDENCE


## 1. Data mode

Every signal, experiment, result, and report row carries the same data mode. Fixture results cannot promote beyond `SIGNAL_VALIDATED_FIXTURE`.

## 2. VectorBT version and limitations

- Availability: vectorbt is not installed
- VectorBT is an optional fast-research adapter. It does not provide final A-share execution evidence for T+1, limit queues, suspension rejection, lots, or exact fills.
- The reference fallback remains available when the optional dependency is absent.

## 3. Candidate strategies

| strategy | top_k | rebalance_days | candidate_count |
| --- | --- | --- | --- |
| topk_equal_weight | 10,20,30,50 | 1,5,10,20 | 4 |
| topk_score_weight | 10,20,30,50 | 1,5,10,20 | 4 |
| rank_weighted | 10,20,30,50 | 1,5,10,20 | 4 |
| topk_dropout | 10,20,30,50 | 1,5,10,20 | 4 |

## 4. TopK comparison

| model | top_k | strategy | cagr | sharpe | sortino | max_drawdown | turnover |
| --- | --- | --- | --- | --- | --- | --- | --- |
| qlib_double_ensemble_alpha158 | 10 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_double_ensemble_alpha158 | 20 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_double_ensemble_alpha158 | 30 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_double_ensemble_alpha158 | 50 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_double_ensemble_alpha158 | 10 | topk_score_weight | 0.028641 | 0.662433 | 0.004179 | -0.022792 | 0.272593 |
| qlib_double_ensemble_alpha158 | 20 | topk_score_weight | 0.028641 | 0.662433 | 0.004179 | -0.022792 | 0.272593 |
| qlib_double_ensemble_alpha158 | 30 | topk_score_weight | 0.028641 | 0.662433 | 0.004179 | -0.022792 | 0.272593 |
| qlib_double_ensemble_alpha158 | 50 | topk_score_weight | 0.028641 | 0.662433 | 0.004179 | -0.022792 | 0.272593 |
| qlib_double_ensemble_alpha158 | 10 | rank_weighted | 0.042358 | 0.909072 | 0.005769 | -0.021917 | 0.123670 |
| qlib_double_ensemble_alpha158 | 20 | rank_weighted | 0.042358 | 0.909072 | 0.005769 | -0.021917 | 0.123670 |
| qlib_double_ensemble_alpha158 | 30 | rank_weighted | 0.042358 | 0.909072 | 0.005769 | -0.021917 | 0.123670 |
| qlib_double_ensemble_alpha158 | 50 | rank_weighted | 0.042358 | 0.909072 | 0.005769 | -0.021917 | 0.123670 |
| qlib_double_ensemble_alpha158 | 10 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_double_ensemble_alpha158 | 20 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_double_ensemble_alpha158 | 30 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_double_ensemble_alpha158 | 50 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_lightgbm_alpha158 | 10 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_lightgbm_alpha158 | 20 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_lightgbm_alpha158 | 30 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_lightgbm_alpha158 | 50 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_lightgbm_alpha158 | 10 | topk_score_weight | 0.039128 | 0.919597 | 0.005867 | -0.023442 | 0.295236 |
| qlib_lightgbm_alpha158 | 20 | topk_score_weight | 0.039128 | 0.919597 | 0.005867 | -0.023442 | 0.295236 |
| qlib_lightgbm_alpha158 | 30 | topk_score_weight | 0.039128 | 0.919597 | 0.005867 | -0.023442 | 0.295236 |
| qlib_lightgbm_alpha158 | 50 | topk_score_weight | 0.039128 | 0.919597 | 0.005867 | -0.023442 | 0.295236 |
| qlib_lightgbm_alpha158 | 10 | rank_weighted | 0.049705 | 1.045800 | 0.006786 | -0.023671 | 0.137431 |
| qlib_lightgbm_alpha158 | 20 | rank_weighted | 0.049705 | 1.045800 | 0.006786 | -0.023671 | 0.137431 |
| qlib_lightgbm_alpha158 | 30 | rank_weighted | 0.049705 | 1.045800 | 0.006786 | -0.023671 | 0.137431 |
| qlib_lightgbm_alpha158 | 50 | rank_weighted | 0.049705 | 1.045800 | 0.006786 | -0.023671 | 0.137431 |
| qlib_lightgbm_alpha158 | 10 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_lightgbm_alpha158 | 20 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_lightgbm_alpha158 | 30 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_lightgbm_alpha158 | 50 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| rule_multifactor | 10 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| rule_multifactor | 20 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| rule_multifactor | 30 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| rule_multifactor | 50 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| rule_multifactor | 10 | topk_score_weight | 0.037024 | 0.867011 | 0.005616 | -0.020262 | 0.198073 |
| rule_multifactor | 20 | topk_score_weight | 0.037024 | 0.867011 | 0.005616 | -0.020262 | 0.198073 |
| rule_multifactor | 30 | topk_score_weight | 0.037024 | 0.867011 | 0.005616 | -0.020262 | 0.198073 |
| rule_multifactor | 50 | topk_score_weight | 0.037024 | 0.867011 | 0.005616 | -0.020262 | 0.198073 |
| rule_multifactor | 10 | rank_weighted | 0.053109 | 1.099596 | 0.007197 | -0.024208 | 0.093853 |
| rule_multifactor | 20 | rank_weighted | 0.053109 | 1.099596 | 0.007197 | -0.024208 | 0.093853 |
| rule_multifactor | 30 | rank_weighted | 0.053109 | 1.099596 | 0.007197 | -0.024208 | 0.093853 |
| rule_multifactor | 50 | rank_weighted | 0.053109 | 1.099596 | 0.007197 | -0.024208 | 0.093853 |
| rule_multifactor | 10 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| rule_multifactor | 20 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| rule_multifactor | 30 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| rule_multifactor | 50 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |

## 5. Rebalance comparison

| model | rebalance_days | strategy | cagr | sharpe | sortino | max_drawdown | turnover |
| --- | --- | --- | --- | --- | --- | --- | --- |
| qlib_double_ensemble_alpha158 | 1 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_double_ensemble_alpha158 | 5 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.026087 |
| qlib_double_ensemble_alpha158 | 10 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.040000 |
| qlib_double_ensemble_alpha158 | 20 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.075000 |
| qlib_double_ensemble_alpha158 | 1 | topk_score_weight | 0.028641 | 0.662433 | 0.004179 | -0.022792 | 0.272593 |
| qlib_double_ensemble_alpha158 | 5 | topk_score_weight | 0.049787 | 1.142151 | 0.007157 | -0.017899 | 0.512609 |
| qlib_double_ensemble_alpha158 | 10 | topk_score_weight | 0.059840 | 1.242988 | 0.008220 | -0.021826 | 0.588200 |
| qlib_double_ensemble_alpha158 | 20 | topk_score_weight | 0.033801 | 0.772898 | 0.004894 | -0.022138 | 0.585827 |
| qlib_double_ensemble_alpha158 | 1 | rank_weighted | 0.042358 | 0.909072 | 0.005769 | -0.021917 | 0.123670 |
| qlib_double_ensemble_alpha158 | 5 | rank_weighted | 0.054874 | 1.128293 | 0.007258 | -0.023671 | 0.281739 |
| qlib_double_ensemble_alpha158 | 10 | rank_weighted | 0.060625 | 1.246470 | 0.008165 | -0.023671 | 0.298667 |
| qlib_double_ensemble_alpha158 | 20 | rank_weighted | 0.050439 | 1.036449 | 0.006643 | -0.024705 | 0.325625 |
| qlib_double_ensemble_alpha158 | 1 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_double_ensemble_alpha158 | 5 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.026087 |
| qlib_double_ensemble_alpha158 | 10 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.040000 |
| qlib_double_ensemble_alpha158 | 20 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.075000 |
| qlib_lightgbm_alpha158 | 1 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_lightgbm_alpha158 | 5 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.026087 |
| qlib_lightgbm_alpha158 | 10 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.040000 |
| qlib_lightgbm_alpha158 | 20 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.075000 |
| qlib_lightgbm_alpha158 | 1 | topk_score_weight | 0.039128 | 0.919597 | 0.005867 | -0.023442 | 0.295236 |
| qlib_lightgbm_alpha158 | 5 | topk_score_weight | 0.036727 | 0.818221 | 0.005201 | -0.024640 | 0.500622 |
| qlib_lightgbm_alpha158 | 10 | topk_score_weight | 0.021312 | 0.531573 | 0.003394 | -0.026218 | 0.654070 |
| qlib_lightgbm_alpha158 | 20 | topk_score_weight | 0.040901 | 0.963670 | 0.006107 | -0.023890 | 0.418009 |
| qlib_lightgbm_alpha158 | 1 | rank_weighted | 0.049705 | 1.045800 | 0.006786 | -0.023671 | 0.137431 |
| qlib_lightgbm_alpha158 | 5 | rank_weighted | 0.044472 | 0.944260 | 0.005964 | -0.023760 | 0.243696 |
| qlib_lightgbm_alpha158 | 10 | rank_weighted | 0.041109 | 0.892500 | 0.005722 | -0.022546 | 0.290333 |
| qlib_lightgbm_alpha158 | 20 | rank_weighted | 0.050387 | 1.071784 | 0.006777 | -0.023760 | 0.263125 |
| qlib_lightgbm_alpha158 | 1 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| qlib_lightgbm_alpha158 | 5 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.026087 |
| qlib_lightgbm_alpha158 | 10 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.040000 |
| qlib_lightgbm_alpha158 | 20 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.075000 |
| rule_multifactor | 1 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| rule_multifactor | 5 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.026087 |
| rule_multifactor | 10 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.040000 |
| rule_multifactor | 20 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.075000 |
| rule_multifactor | 1 | topk_score_weight | 0.037024 | 0.867011 | 0.005616 | -0.020262 | 0.198073 |
| rule_multifactor | 5 | topk_score_weight | 0.045428 | 0.943112 | 0.006177 | -0.027530 | 0.299326 |
| rule_multifactor | 10 | topk_score_weight | 0.030362 | 0.705804 | 0.004616 | -0.023521 | 0.342991 |
| rule_multifactor | 20 | topk_score_weight | 0.033964 | 0.746950 | 0.004776 | -0.024604 | 0.488178 |
| rule_multifactor | 1 | rank_weighted | 0.053109 | 1.099596 | 0.007197 | -0.024208 | 0.093853 |
| rule_multifactor | 5 | rank_weighted | 0.051072 | 1.062866 | 0.006981 | -0.024208 | 0.173043 |
| rule_multifactor | 10 | rank_weighted | 0.045725 | 0.953932 | 0.006173 | -0.024208 | 0.157000 |
| rule_multifactor | 20 | rank_weighted | 0.041753 | 0.886571 | 0.005740 | -0.024208 | 0.278750 |
| rule_multifactor | 1 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.005505 |
| rule_multifactor | 5 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.026087 |
| rule_multifactor | 10 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.040000 |
| rule_multifactor | 20 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.075000 |

## 6. Turnover comparison

| strategy | raw_turnover | normalized_turnover | estimated_transaction_cost |
| --- | --- | --- | --- |
| topk_equal_weight | 0.005505 | 0.005505 | 0.000480 |
| topk_score_weight | 0.112256 | 0.295236 | 0.012753 |
| rank_weighted | 0.068257 | 0.137431 | 0.007692 |
| topk_dropout | 0.005505 | 0.005505 | 0.000480 |

## 7. Transaction cost estimates

| strategy | estimated_transaction_cost | total_return |
| --- | --- | --- |
| topk_equal_weight | 0.000480 | 0.056384 |
| topk_score_weight | 0.012753 | 0.038664 |
| rank_weighted | 0.007692 | 0.049112 |
| topk_dropout | 0.000480 | 0.056384 |

EqualWeight vs ScoreWeight (Score minus Equal):

| model | cagr_delta_score_minus_equal | turnover_delta_score_minus_equal | cost_delta_score_minus_equal |
| --- | --- | --- | --- |
| qlib_lightgbm_alpha158 | -0.017939 | 0.289732 | 0.012273 |
| qlib_double_ensemble_alpha158 | -0.028425 | 0.267088 | 0.011520 |

## 8. Drawdown comparison

| model | strategy | max_drawdown | calmar | volatility |
| --- | --- | --- | --- | --- |
| qlib_lightgbm_alpha158 | topk_equal_weight | -0.029451 | 1.937643 | 0.055052 |
| qlib_lightgbm_alpha158 | topk_score_weight | -0.023442 | 1.669150 | 0.041219 |
| qlib_lightgbm_alpha158 | rank_weighted | -0.023671 | 2.099766 | 0.045785 |
| qlib_lightgbm_alpha158 | topk_dropout | -0.029451 | 1.937643 | 0.055052 |

## 9. Parameter surface

| model | strategy | top_k | rebalance_days | cagr | sharpe | sortino | max_drawdown | volatility | turnover |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 10 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 10 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 10 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 10 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 20 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 20 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 20 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 20 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 30 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 30 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 30 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 30 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 50 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 50 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 50 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 50 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 10 | 1 | 0.028641 | 0.662433 | 0.004179 | -0.022792 | 0.042526 | 0.272593 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 10 | 5 | 0.049787 | 1.142151 | 0.007157 | -0.017899 | 0.041835 | 0.512609 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 10 | 10 | 0.059840 | 1.242988 | 0.008220 | -0.021826 | 0.045994 | 0.588200 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 10 | 20 | 0.033801 | 0.772898 | 0.004894 | -0.022138 | 0.042705 | 0.585827 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 20 | 1 | 0.028641 | 0.662433 | 0.004179 | -0.022792 | 0.042526 | 0.272593 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 20 | 5 | 0.049787 | 1.142151 | 0.007157 | -0.017899 | 0.041835 | 0.512609 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 20 | 10 | 0.059840 | 1.242988 | 0.008220 | -0.021826 | 0.045994 | 0.588200 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 20 | 20 | 0.033801 | 0.772898 | 0.004894 | -0.022138 | 0.042705 | 0.585827 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 30 | 1 | 0.028641 | 0.662433 | 0.004179 | -0.022792 | 0.042526 | 0.272593 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 30 | 5 | 0.049787 | 1.142151 | 0.007157 | -0.017899 | 0.041835 | 0.512609 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 30 | 10 | 0.059840 | 1.242988 | 0.008220 | -0.021826 | 0.045994 | 0.588200 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 30 | 20 | 0.033801 | 0.772898 | 0.004894 | -0.022138 | 0.042705 | 0.585827 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 50 | 1 | 0.028641 | 0.662433 | 0.004179 | -0.022792 | 0.042526 | 0.272593 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 50 | 5 | 0.049787 | 1.142151 | 0.007157 | -0.017899 | 0.041835 | 0.512609 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 50 | 10 | 0.059840 | 1.242988 | 0.008220 | -0.021826 | 0.045994 | 0.588200 |
| qlib_double_ensemble_alpha158 | topk_score_weight | 50 | 20 | 0.033801 | 0.772898 | 0.004894 | -0.022138 | 0.042705 | 0.585827 |
| qlib_double_ensemble_alpha158 | rank_weighted | 10 | 1 | 0.042358 | 0.909072 | 0.005769 | -0.021917 | 0.045185 | 0.123670 |
| qlib_double_ensemble_alpha158 | rank_weighted | 10 | 5 | 0.054874 | 1.128293 | 0.007258 | -0.023671 | 0.046676 | 0.281739 |
| qlib_double_ensemble_alpha158 | rank_weighted | 10 | 10 | 0.060625 | 1.246470 | 0.008165 | -0.023671 | 0.046456 | 0.298667 |
| qlib_double_ensemble_alpha158 | rank_weighted | 10 | 20 | 0.050439 | 1.036449 | 0.006643 | -0.024705 | 0.046901 | 0.325625 |
| qlib_double_ensemble_alpha158 | rank_weighted | 20 | 1 | 0.042358 | 0.909072 | 0.005769 | -0.021917 | 0.045185 | 0.123670 |
| qlib_double_ensemble_alpha158 | rank_weighted | 20 | 5 | 0.054874 | 1.128293 | 0.007258 | -0.023671 | 0.046676 | 0.281739 |
| qlib_double_ensemble_alpha158 | rank_weighted | 20 | 10 | 0.060625 | 1.246470 | 0.008165 | -0.023671 | 0.046456 | 0.298667 |
| qlib_double_ensemble_alpha158 | rank_weighted | 20 | 20 | 0.050439 | 1.036449 | 0.006643 | -0.024705 | 0.046901 | 0.325625 |
| qlib_double_ensemble_alpha158 | rank_weighted | 30 | 1 | 0.042358 | 0.909072 | 0.005769 | -0.021917 | 0.045185 | 0.123670 |
| qlib_double_ensemble_alpha158 | rank_weighted | 30 | 5 | 0.054874 | 1.128293 | 0.007258 | -0.023671 | 0.046676 | 0.281739 |
| qlib_double_ensemble_alpha158 | rank_weighted | 30 | 10 | 0.060625 | 1.246470 | 0.008165 | -0.023671 | 0.046456 | 0.298667 |
| qlib_double_ensemble_alpha158 | rank_weighted | 30 | 20 | 0.050439 | 1.036449 | 0.006643 | -0.024705 | 0.046901 | 0.325625 |
| qlib_double_ensemble_alpha158 | rank_weighted | 50 | 1 | 0.042358 | 0.909072 | 0.005769 | -0.021917 | 0.045185 | 0.123670 |
| qlib_double_ensemble_alpha158 | rank_weighted | 50 | 5 | 0.054874 | 1.128293 | 0.007258 | -0.023671 | 0.046676 | 0.281739 |
| qlib_double_ensemble_alpha158 | rank_weighted | 50 | 10 | 0.060625 | 1.246470 | 0.008165 | -0.023671 | 0.046456 | 0.298667 |
| qlib_double_ensemble_alpha158 | rank_weighted | 50 | 20 | 0.050439 | 1.036449 | 0.006643 | -0.024705 | 0.046901 | 0.325625 |
| qlib_double_ensemble_alpha158 | topk_dropout | 10 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_double_ensemble_alpha158 | topk_dropout | 10 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_double_ensemble_alpha158 | topk_dropout | 10 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_double_ensemble_alpha158 | topk_dropout | 10 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_double_ensemble_alpha158 | topk_dropout | 20 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_double_ensemble_alpha158 | topk_dropout | 20 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_double_ensemble_alpha158 | topk_dropout | 20 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_double_ensemble_alpha158 | topk_dropout | 20 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_double_ensemble_alpha158 | topk_dropout | 30 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_double_ensemble_alpha158 | topk_dropout | 30 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_double_ensemble_alpha158 | topk_dropout | 30 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_double_ensemble_alpha158 | topk_dropout | 30 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_double_ensemble_alpha158 | topk_dropout | 50 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_double_ensemble_alpha158 | topk_dropout | 50 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_double_ensemble_alpha158 | topk_dropout | 50 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_double_ensemble_alpha158 | topk_dropout | 50 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 10 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 10 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 10 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 10 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 20 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 20 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 20 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 20 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 30 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 30 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 30 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 30 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 50 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 50 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 50 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_lightgbm_alpha158 | topk_equal_weight | 50 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_lightgbm_alpha158 | topk_score_weight | 10 | 1 | 0.039128 | 0.919597 | 0.005867 | -0.023442 | 0.041219 | 0.295236 |
| qlib_lightgbm_alpha158 | topk_score_weight | 10 | 5 | 0.036727 | 0.818221 | 0.005201 | -0.024640 | 0.043720 | 0.500622 |
| qlib_lightgbm_alpha158 | topk_score_weight | 10 | 10 | 0.021312 | 0.531573 | 0.003394 | -0.026218 | 0.039792 | 0.654070 |
| qlib_lightgbm_alpha158 | topk_score_weight | 10 | 20 | 0.040901 | 0.963670 | 0.006107 | -0.023890 | 0.041034 | 0.418009 |
| qlib_lightgbm_alpha158 | topk_score_weight | 20 | 1 | 0.039128 | 0.919597 | 0.005867 | -0.023442 | 0.041219 | 0.295236 |
| qlib_lightgbm_alpha158 | topk_score_weight | 20 | 5 | 0.036727 | 0.818221 | 0.005201 | -0.024640 | 0.043720 | 0.500622 |
| qlib_lightgbm_alpha158 | topk_score_weight | 20 | 10 | 0.021312 | 0.531573 | 0.003394 | -0.026218 | 0.039792 | 0.654070 |
| qlib_lightgbm_alpha158 | topk_score_weight | 20 | 20 | 0.040901 | 0.963670 | 0.006107 | -0.023890 | 0.041034 | 0.418009 |
| qlib_lightgbm_alpha158 | topk_score_weight | 30 | 1 | 0.039128 | 0.919597 | 0.005867 | -0.023442 | 0.041219 | 0.295236 |
| qlib_lightgbm_alpha158 | topk_score_weight | 30 | 5 | 0.036727 | 0.818221 | 0.005201 | -0.024640 | 0.043720 | 0.500622 |
| qlib_lightgbm_alpha158 | topk_score_weight | 30 | 10 | 0.021312 | 0.531573 | 0.003394 | -0.026218 | 0.039792 | 0.654070 |
| qlib_lightgbm_alpha158 | topk_score_weight | 30 | 20 | 0.040901 | 0.963670 | 0.006107 | -0.023890 | 0.041034 | 0.418009 |
| qlib_lightgbm_alpha158 | topk_score_weight | 50 | 1 | 0.039128 | 0.919597 | 0.005867 | -0.023442 | 0.041219 | 0.295236 |
| qlib_lightgbm_alpha158 | topk_score_weight | 50 | 5 | 0.036727 | 0.818221 | 0.005201 | -0.024640 | 0.043720 | 0.500622 |
| qlib_lightgbm_alpha158 | topk_score_weight | 50 | 10 | 0.021312 | 0.531573 | 0.003394 | -0.026218 | 0.039792 | 0.654070 |
| qlib_lightgbm_alpha158 | topk_score_weight | 50 | 20 | 0.040901 | 0.963670 | 0.006107 | -0.023890 | 0.041034 | 0.418009 |
| qlib_lightgbm_alpha158 | rank_weighted | 10 | 1 | 0.049705 | 1.045800 | 0.006786 | -0.023671 | 0.045785 | 0.137431 |
| qlib_lightgbm_alpha158 | rank_weighted | 10 | 5 | 0.044472 | 0.944260 | 0.005964 | -0.023760 | 0.045585 | 0.243696 |
| qlib_lightgbm_alpha158 | rank_weighted | 10 | 10 | 0.041109 | 0.892500 | 0.005722 | -0.022546 | 0.044700 | 0.290333 |
| qlib_lightgbm_alpha158 | rank_weighted | 10 | 20 | 0.050387 | 1.071784 | 0.006777 | -0.023760 | 0.045238 | 0.263125 |
| qlib_lightgbm_alpha158 | rank_weighted | 20 | 1 | 0.049705 | 1.045800 | 0.006786 | -0.023671 | 0.045785 | 0.137431 |
| qlib_lightgbm_alpha158 | rank_weighted | 20 | 5 | 0.044472 | 0.944260 | 0.005964 | -0.023760 | 0.045585 | 0.243696 |
| qlib_lightgbm_alpha158 | rank_weighted | 20 | 10 | 0.041109 | 0.892500 | 0.005722 | -0.022546 | 0.044700 | 0.290333 |
| qlib_lightgbm_alpha158 | rank_weighted | 20 | 20 | 0.050387 | 1.071784 | 0.006777 | -0.023760 | 0.045238 | 0.263125 |
| qlib_lightgbm_alpha158 | rank_weighted | 30 | 1 | 0.049705 | 1.045800 | 0.006786 | -0.023671 | 0.045785 | 0.137431 |
| qlib_lightgbm_alpha158 | rank_weighted | 30 | 5 | 0.044472 | 0.944260 | 0.005964 | -0.023760 | 0.045585 | 0.243696 |
| qlib_lightgbm_alpha158 | rank_weighted | 30 | 10 | 0.041109 | 0.892500 | 0.005722 | -0.022546 | 0.044700 | 0.290333 |
| qlib_lightgbm_alpha158 | rank_weighted | 30 | 20 | 0.050387 | 1.071784 | 0.006777 | -0.023760 | 0.045238 | 0.263125 |
| qlib_lightgbm_alpha158 | rank_weighted | 50 | 1 | 0.049705 | 1.045800 | 0.006786 | -0.023671 | 0.045785 | 0.137431 |
| qlib_lightgbm_alpha158 | rank_weighted | 50 | 5 | 0.044472 | 0.944260 | 0.005964 | -0.023760 | 0.045585 | 0.243696 |
| qlib_lightgbm_alpha158 | rank_weighted | 50 | 10 | 0.041109 | 0.892500 | 0.005722 | -0.022546 | 0.044700 | 0.290333 |
| qlib_lightgbm_alpha158 | rank_weighted | 50 | 20 | 0.050387 | 1.071784 | 0.006777 | -0.023760 | 0.045238 | 0.263125 |
| qlib_lightgbm_alpha158 | topk_dropout | 10 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_lightgbm_alpha158 | topk_dropout | 10 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_lightgbm_alpha158 | topk_dropout | 10 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_lightgbm_alpha158 | topk_dropout | 10 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_lightgbm_alpha158 | topk_dropout | 20 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_lightgbm_alpha158 | topk_dropout | 20 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_lightgbm_alpha158 | topk_dropout | 20 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_lightgbm_alpha158 | topk_dropout | 20 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_lightgbm_alpha158 | topk_dropout | 30 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_lightgbm_alpha158 | topk_dropout | 30 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_lightgbm_alpha158 | topk_dropout | 30 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_lightgbm_alpha158 | topk_dropout | 30 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| qlib_lightgbm_alpha158 | topk_dropout | 50 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| qlib_lightgbm_alpha158 | topk_dropout | 50 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| qlib_lightgbm_alpha158 | topk_dropout | 50 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| qlib_lightgbm_alpha158 | topk_dropout | 50 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| rule_multifactor | topk_equal_weight | 10 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| rule_multifactor | topk_equal_weight | 10 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| rule_multifactor | topk_equal_weight | 10 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| rule_multifactor | topk_equal_weight | 10 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| rule_multifactor | topk_equal_weight | 20 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| rule_multifactor | topk_equal_weight | 20 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| rule_multifactor | topk_equal_weight | 20 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| rule_multifactor | topk_equal_weight | 20 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| rule_multifactor | topk_equal_weight | 30 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| rule_multifactor | topk_equal_weight | 30 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| rule_multifactor | topk_equal_weight | 30 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| rule_multifactor | topk_equal_weight | 30 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| rule_multifactor | topk_equal_weight | 50 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| rule_multifactor | topk_equal_weight | 50 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| rule_multifactor | topk_equal_weight | 50 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| rule_multifactor | topk_equal_weight | 50 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| rule_multifactor | topk_score_weight | 10 | 1 | 0.037024 | 0.867011 | 0.005616 | -0.020262 | 0.041479 | 0.198073 |
| rule_multifactor | topk_score_weight | 10 | 5 | 0.045428 | 0.943112 | 0.006177 | -0.027530 | 0.046631 | 0.299326 |
| rule_multifactor | topk_score_weight | 10 | 10 | 0.030362 | 0.705804 | 0.004616 | -0.023521 | 0.042173 | 0.342991 |
| rule_multifactor | topk_score_weight | 10 | 20 | 0.033964 | 0.746950 | 0.004776 | -0.024604 | 0.044491 | 0.488178 |
| rule_multifactor | topk_score_weight | 20 | 1 | 0.037024 | 0.867011 | 0.005616 | -0.020262 | 0.041479 | 0.198073 |
| rule_multifactor | topk_score_weight | 20 | 5 | 0.045428 | 0.943112 | 0.006177 | -0.027530 | 0.046631 | 0.299326 |
| rule_multifactor | topk_score_weight | 20 | 10 | 0.030362 | 0.705804 | 0.004616 | -0.023521 | 0.042173 | 0.342991 |
| rule_multifactor | topk_score_weight | 20 | 20 | 0.033964 | 0.746950 | 0.004776 | -0.024604 | 0.044491 | 0.488178 |
| rule_multifactor | topk_score_weight | 30 | 1 | 0.037024 | 0.867011 | 0.005616 | -0.020262 | 0.041479 | 0.198073 |
| rule_multifactor | topk_score_weight | 30 | 5 | 0.045428 | 0.943112 | 0.006177 | -0.027530 | 0.046631 | 0.299326 |
| rule_multifactor | topk_score_weight | 30 | 10 | 0.030362 | 0.705804 | 0.004616 | -0.023521 | 0.042173 | 0.342991 |
| rule_multifactor | topk_score_weight | 30 | 20 | 0.033964 | 0.746950 | 0.004776 | -0.024604 | 0.044491 | 0.488178 |
| rule_multifactor | topk_score_weight | 50 | 1 | 0.037024 | 0.867011 | 0.005616 | -0.020262 | 0.041479 | 0.198073 |
| rule_multifactor | topk_score_weight | 50 | 5 | 0.045428 | 0.943112 | 0.006177 | -0.027530 | 0.046631 | 0.299326 |
| rule_multifactor | topk_score_weight | 50 | 10 | 0.030362 | 0.705804 | 0.004616 | -0.023521 | 0.042173 | 0.342991 |
| rule_multifactor | topk_score_weight | 50 | 20 | 0.033964 | 0.746950 | 0.004776 | -0.024604 | 0.044491 | 0.488178 |
| rule_multifactor | rank_weighted | 10 | 1 | 0.053109 | 1.099596 | 0.007197 | -0.024208 | 0.046417 | 0.093853 |
| rule_multifactor | rank_weighted | 10 | 5 | 0.051072 | 1.062866 | 0.006981 | -0.024208 | 0.046253 | 0.173043 |
| rule_multifactor | rank_weighted | 10 | 10 | 0.045725 | 0.953932 | 0.006173 | -0.024208 | 0.046381 | 0.157000 |
| rule_multifactor | rank_weighted | 10 | 20 | 0.041753 | 0.886571 | 0.005740 | -0.024208 | 0.045723 | 0.278750 |
| rule_multifactor | rank_weighted | 20 | 1 | 0.053109 | 1.099596 | 0.007197 | -0.024208 | 0.046417 | 0.093853 |
| rule_multifactor | rank_weighted | 20 | 5 | 0.051072 | 1.062866 | 0.006981 | -0.024208 | 0.046253 | 0.173043 |
| rule_multifactor | rank_weighted | 20 | 10 | 0.045725 | 0.953932 | 0.006173 | -0.024208 | 0.046381 | 0.157000 |
| rule_multifactor | rank_weighted | 20 | 20 | 0.041753 | 0.886571 | 0.005740 | -0.024208 | 0.045723 | 0.278750 |
| rule_multifactor | rank_weighted | 30 | 1 | 0.053109 | 1.099596 | 0.007197 | -0.024208 | 0.046417 | 0.093853 |
| rule_multifactor | rank_weighted | 30 | 5 | 0.051072 | 1.062866 | 0.006981 | -0.024208 | 0.046253 | 0.173043 |
| rule_multifactor | rank_weighted | 30 | 10 | 0.045725 | 0.953932 | 0.006173 | -0.024208 | 0.046381 | 0.157000 |
| rule_multifactor | rank_weighted | 30 | 20 | 0.041753 | 0.886571 | 0.005740 | -0.024208 | 0.045723 | 0.278750 |
| rule_multifactor | rank_weighted | 50 | 1 | 0.053109 | 1.099596 | 0.007197 | -0.024208 | 0.046417 | 0.093853 |
| rule_multifactor | rank_weighted | 50 | 5 | 0.051072 | 1.062866 | 0.006981 | -0.024208 | 0.046253 | 0.173043 |
| rule_multifactor | rank_weighted | 50 | 10 | 0.045725 | 0.953932 | 0.006173 | -0.024208 | 0.046381 | 0.157000 |
| rule_multifactor | rank_weighted | 50 | 20 | 0.041753 | 0.886571 | 0.005740 | -0.024208 | 0.045723 | 0.278750 |
| rule_multifactor | topk_dropout | 10 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| rule_multifactor | topk_dropout | 10 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| rule_multifactor | topk_dropout | 10 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| rule_multifactor | topk_dropout | 10 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| rule_multifactor | topk_dropout | 20 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| rule_multifactor | topk_dropout | 20 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| rule_multifactor | topk_dropout | 20 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| rule_multifactor | topk_dropout | 20 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| rule_multifactor | topk_dropout | 30 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| rule_multifactor | topk_dropout | 30 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| rule_multifactor | topk_dropout | 30 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| rule_multifactor | topk_dropout | 30 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |
| rule_multifactor | topk_dropout | 50 | 1 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 |
| rule_multifactor | topk_dropout | 50 | 5 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.026087 |
| rule_multifactor | topk_dropout | 50 | 10 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.040000 |
| rule_multifactor | topk_dropout | 50 | 20 | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.075000 |

- Parameter Island: `not identified: four-symbol fixture universe and no historical evidence`
- TopK zone: `Not distinguishable in the four-symbol fixture universe; historical breadth is required.`
- Rebalance zone: `Five to ten calendar days is a diagnostic stability zone for some fixture rows; not a production conclusion.`
- The grid is intentionally small and diagnostic; it is not a return-maximizing optimizer.

## 10. Model x strategy matrix

| model | strategy | cagr | sharpe | sortino | max_drawdown | volatility | turnover | selection_status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| qlib_double_ensemble_alpha158 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 | CANDIDATE |
| qlib_double_ensemble_alpha158 | topk_score_weight | 0.028641 | 0.662433 | 0.004179 | -0.022792 | 0.042526 | 0.272593 | CANDIDATE |
| qlib_double_ensemble_alpha158 | rank_weighted | 0.042358 | 0.909072 | 0.005769 | -0.021917 | 0.045185 | 0.123670 | CANDIDATE |
| qlib_double_ensemble_alpha158 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 | CANDIDATE |
| qlib_lightgbm_alpha158 | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 | CANDIDATE |
| qlib_lightgbm_alpha158 | topk_score_weight | 0.039128 | 0.919597 | 0.005867 | -0.023442 | 0.041219 | 0.295236 | CANDIDATE |
| qlib_lightgbm_alpha158 | rank_weighted | 0.049705 | 1.045800 | 0.006786 | -0.023671 | 0.045785 | 0.137431 | CANDIDATE |
| qlib_lightgbm_alpha158 | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 | CANDIDATE |
| rule_multifactor | topk_equal_weight | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 | ENSEMBLE_INELIGIBLE |
| rule_multifactor | topk_score_weight | 0.037024 | 0.867011 | 0.005616 | -0.020262 | 0.041479 | 0.198073 | ENSEMBLE_INELIGIBLE |
| rule_multifactor | rank_weighted | 0.053109 | 1.099596 | 0.007197 | -0.024208 | 0.046417 | 0.093853 | ENSEMBLE_INELIGIBLE |
| rule_multifactor | topk_dropout | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 | ENSEMBLE_INELIGIBLE |

## 11. EqualRank Ensemble fixture result

| strategy | cagr | sharpe | sortino | max_drawdown | volatility | turnover | selection_status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| equal_rank_ensemble_fixture | 0.057066 | 1.000871 | 0.006556 | -0.029451 | 0.055052 | 0.005505 | FIXTURE_PIPELINE_ONLY |

## 12. Historical dry run

- Status: `NOT_RUN`
- Missing: historical bars for candidate symbols: 000002, 000003, 000004
- Missing: historical benchmark snapshot: 000300
- Note: Historical dry run was not promoted or replaced by fixture results.

## 13. Known limitations

- All Stage 3B numbers use the accepted Stage 2 fixture artifacts and synthetic fixture bars.
- Fixture results are diagnostic only and cannot promote to FAST_BACKTEST_PASS or PAPER_TRADING.
- RuleBasedMultiFactor is retained but marked ENSEMBLE_INELIGIBLE for this weak fixture signal.
- VectorBT is optional; the current machine may use the reference fallback.
- No large Optuna search, weighted ensemble, RQAlpha validation, broker, or live order path is included.

## 14. Next-stage recommendation

Stop after Stage 3B. Stage 3C may add equal-rank ensemble research, correlation controls, robust optimization, and nested walk-forward only after historical data and benchmark coverage are available.
