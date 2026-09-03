# Bloub runtime origin

- Source: https://github.com/jeremy-prt/bloub
- Revision: b4bb3c1
- Upstream version: 0.1.1
- License: MIT, see `LICENSE`

Product Atelier vendors only the framework-free animation engine modules from
`src/bot`. The Vue interface, timeline editor, exporters, media encoder, styles,
and upstream brand presentation are not included.

The TypeScript sources were mechanically transpiled to browser ESM with esbuild.
Product Atelier supplies its own task-state mapping, interaction controller,
rendering adapter, colors, sizing, and accessible behavior.
