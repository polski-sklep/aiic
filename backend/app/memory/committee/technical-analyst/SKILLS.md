# SKILLS

## Skill 1: Confluence Location
### Purpose
Find the price levels where several independent things agree.
### Procedure
1. Take structural levels from `compute_technical_levels` at `1d`, then at `4h`.
2. Overlay resting size from `get_orderbook_depth`.
3. Keep only levels where at least two independent sources coincide. Discard
   the rest — a lone level is not a level.
### Output
A short list of levels, each with the reason it is one.

## Skill 2: Entry Laddering
### Purpose
Turn levels into a plan that does not require being right about direction.
### Procedure
1. Assign 2–3 zones, aggressive through conservative, summing to 100% of the
   intended position.
2. Size each zone by the confluence supporting it, not by preference.
3. Give each zone an invalidation.
### Output
The entry ladder, in the required JSON shape.

## Skill 3: Invalidation Statement
### Purpose
Make the plan falsifiable before anyone uses it.
### Procedure
1. For each zone, state the price action that would prove the level failed.
2. Use the ATR to separate noise from a real break.
3. If a zone has no clean invalidation, drop it rather than widen it.
### Output
One invalidation per zone, stated as a price.

## Skill 4: Execution Verdict
### Purpose
Say plainly whether buying at the current price is good execution.
### Procedure
1. Compare the current price to the ladder.
2. Rate current-price entry quality against the SOUL rubric.
3. Name one recommended strategy — market, limit ladder, wait for retracement,
   wait for breakout confirmation.
### Output
An entry-quality rating and a single strategy.
