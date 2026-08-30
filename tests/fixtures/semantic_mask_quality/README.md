# Semantic mask quality smoke set

This manifest reuses the three source-controlled licensed photos in
`../semantic_grounding_photos/`. Source URLs, authors, licenses, file hashes,
and attribution remain locked in that directory's `manifest.json` and README.

The set has confirmed object regions but no pixel-level ground-truth alpha
masks. It can gate empty output, region leakage, loss of soft alpha levels,
runtime regressions, and include/exclude correction recovery. It cannot measure
edge accuracy, halo accuracy, hair/transparency fidelity, or production-ready
commercial quality. Those claims require manually reviewed pixel masks or a
documented human visual review protocol.
