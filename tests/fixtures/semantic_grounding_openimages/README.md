# Open Images V7 semantic-grounding gate

This directory locks the selection, official bounding boxes, official negative labels, image metadata, exact byte sizes, SHA-256 hashes, and Chinese-to-model queries for a 30-photo / 35-query validation gate. The image pixels are downloaded on demand and are not redistributed through Git.

The Open Images V7 description lists annotations as CC BY 4.0 and images as CC BY 2.0, while warning that each image's license should still be checked. `manifest.json` therefore preserves the author, source page, license URL, original URL, and exact CVDF mirror hash for every selected image.

Run from the repository root:

```powershell
python tools\bootstrap_semantic_grounding_corpus.py --download
python tools\bootstrap_semantic_grounding_corpus.py --verify
python tools\evaluate_semantic_grounding.py --manifest tests\fixtures\semantic_grounding_openimages\manifest.json --run-local --query-field query --resolve-query
```

The checked-in low-threshold RTX 4060 predictions can be re-scored without the model or image pixels:

```powershell
python tools\calibrate_semantic_grounding.py --manifest tests\fixtures\semantic_grounding_openimages\manifest.json --predictions docs\reports\semantic-grounding-openimages-zh-mapped-threshold-040-predictions-rtx4060-2026-08-29.json --thresholds 0.40,0.45,0.50,0.55,0.60,0.65,0.67,0.70,0.72,0.75
```

The default cache is `build/eval-corpora/product-atelier-open-images-v7-validation-v1`. Use the same `--destination` / `--image-root` value when choosing a different cache location.

This corpus evaluates name-driven object localization and count safety. It does not contain ground-truth alpha masks, so it cannot prove hair, transparent-edge, or fine-line cutout quality by itself.
