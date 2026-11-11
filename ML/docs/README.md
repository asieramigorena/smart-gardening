# Irrigation model

**Assumption:** water flow `Q` (L/h) is constant, so the water delivered depends only on how long the valve stays open.

## Success metric (must satisfy)

- ≥ 95 % of hours with root-zone moisture between θ_refill and θ_FC (§1).
- Hours above θ_FC count only if they occur within 24 h after an irrigation. Rain-driven exceedance is not the controller's fault.
- 0 hours below θ_WP + 0.02.

## Optimization metric (minimize)

- Water used = Σ (valve-open time × Q), reported in L/m² per season.
- Always reported relative to the baseline threshold controller (irrigate when θ < θ_refill).

## Forest

### Inputs

All weather forecasts are used **as issued** at decision time, never the observed values that came later.

| Group | Features |
|---|---|
| Time | hour (sin/cos), day of year (sin/cos), days since planting |
| Moisture | θ at each sensor depth; Δθ over the last 1 h / 6 h / 24 h |
| Irrigation history | hours since last irrigation; duration of last opening |
| Observed weather | rain over the last 24 h / 72 h / 7 d; ET₀ over the last 24 h / 72 h; current radiation |
| Forecast | rain over the next 6 h / 24 h / 48 h / 72 h; max rain probability over the next 24 h; ET₀ over the next 24 h / 72 h; radiation over the next 24 h |
| Bed | soil type, crop, root depth |

### Output

- Predicted θ at +6 h / +24 h / +48 h, **assuming no irrigation**, as median and 10th percentile (quantile regression forest).

## Behavior tree

### Inputs

- Forest predictions
- Current θ and sensor health flags (dropout, stuck value, spike, out of range; §9)
- Rain forecast (amount and probability)

### Output

- Valve-open duration in seconds (0 = no irrigation):

  ```
  t = λ · 1000 · (θ_FC − θ) · Z_r · A / (E_a · Q)      [h; × 3600 for s]
  ```

  λ = refill fraction, Z_r = root depth (m), A = bed area (m²), E_a = application efficiency (§8).
- Split into pulses with soak time when Q / A exceeds the soil's intake rate (§8).
