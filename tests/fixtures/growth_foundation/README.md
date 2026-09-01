# Growth foundation fixtures

This directory contains synthetic contract data for G0 and the isolated G1A prototype. It must never read or copy `%APPDATA%\ProductAtelier`, API keys, user prompts, user images, generated results, or the formal SQLite ledger.

`minimal-contract.json` exercises the nine frozen draft contracts in `docs/contracts/growth-foundation-v1.schema.json`. All asset, result, proxy, mask, product, quality, and recipe identities are synthetic. The fixture references metadata only; it does not embed Base64 image bytes or absolute local paths.

JSON Schema enforces the structural boundary. `Operation.mutation` stores strict before/after layer snapshots so transform, visibility, lock, and z-order history can survive a restart without a private side channel. `tests/test_growth_foundation_contract.py` also freezes semantic invariants that JSON Schema cannot express by itself: referenced entities must exist, entity IDs must be unique within each collection, asset layers must use declared source assets, mutation targets must exist, ROI rectangles must stay inside normalized source bounds, and the undo cursor must stay inside operation history.

The schema is a growth-prototype boundary, not a production database migration. G1A may load copies of this fixture from an isolated prototype directory, but it must not write to the production `src/`, formal portable directory, or user data directories.
