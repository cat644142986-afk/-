# Generation quality baseline

This fixture set freezes the offline inputs and blind-review contract for R7. It does not contain user images and it does not claim that any model has passed. The three real photos reuse the source and license receipts in `semantic_grounding_photos/`; six small procedural PNGs cover exact text, count, brand color, reflective material, vessel preservation, truncation completion, and complex-shadow cases without paid generation.

`python tools/render_generation_quality_fixtures.py --write` creates the deterministic procedural files. A normal verification run omits `--write` and fails if a file or hash has drifted.

Paid A/B remains a separate gate. Before running it, freeze the call count, budget, stop conditions, model snapshots, prompt versions, and randomized reviewer labels. Generated candidates and scores must not overwrite this input manifest.

`experiment-template.json` is intentionally not authorized and has a zero paid-call budget. After the user explicitly approves a frozen budget, copy it to a run-specific plan and change only the approved fields. Build the reviewer-safe packet and separate private mapping with:

```powershell
python tools/build_generation_blind_review.py --plan <run-plan.json> --seed <private-seed> --reviewer-output <review.json> --mapping-output <private-map.json>
```

The command only prepares evidence; it never calls a generation provider. It refuses to overwrite existing outputs unless `--force` is explicit.
