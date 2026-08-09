# Stage 3A Signal Quality Report

- Data mode: `fixture`
- Source commit: `a293a13`
- Label: `forward_return`

> TEST / FIXTURE DATA - NOT INVESTMENT EVIDENCE

- This report is diagnostic evidence; Stage 3A uses structural gates and does not invent return thresholds.

## Model summary

| Strategy | Sample count | IC mean | Rank IC mean | ICIR | Positive IC ratio | Diagnostics |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| qlib_double_ensemble_alpha158 | 436 | 0.089327 | 0.075229 | 0.155521 | 0.596330 | none |
| qlib_lightgbm_alpha158 | 436 | 0.122396 | 0.078564 | 0.225055 | 0.587156 | none |
| rule_multifactor | 436 | -0.027170 | -0.016514 | -0.047688 | 0.486239 | WEAK_SIGNAL_MONOTONICITY |

## Model correlation

| Left | Right | Score correlation | Rank correlation | Top-K overlap | Portfolio return correlation |
| --- | --- | ---: | ---: | ---: | ---: |
| qlib_double_ensemble_alpha158 | qlib_lightgbm_alpha158 | 0.662982 | 0.538346 | 1.000000 | n/a |
| qlib_double_ensemble_alpha158 | rule_multifactor | 0.225226 | 0.192661 | 1.000000 | n/a |
| qlib_lightgbm_alpha158 | rule_multifactor | 0.222519 | 0.180212 | 1.000000 | n/a |

## Limitations

- Results are only as valid as the frozen source artifacts.
- The accepted local Stage 2 bundles are fixture data.
