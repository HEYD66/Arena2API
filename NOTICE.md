# Attribution and distribution changes

Based on Multfinite/Arena2API: https://github.com/Multfinite/Arena2API . The original GPL v3 license text is preserved in LICENSE. Existing source notices remain in place.

Distribution changes dated 2026-10-19: private runtime/history excluded, public documentation rewritten, tool inventory replaced with an empty template, installed-host tests parameterized, desktop sandbox-disable override removed. The independently developed dsh-step-viewer plugin is GPL-3.0-only by owner authorization; complete plugin source/build script included. Third-party Python/Node/DSH/React runtimes are not bundled and retain their own licenses.

Portable edition 2026-10-19: bundled unmodified Python/Node/Qt runtimes and Python dependencies under their respective licenses; project launcher/runtime path adaptation source is included. See THIRD-PARTY-SOURCES.json and docs/便携版构建与许可.md.

Portable edition 2026-10-29: rebuilt from the project working tree at commit b1531d6 with the same bundled runtimes. Changes since 2026-10-19 are listed in README.md. Distribution adaptations re-applied: portable runtime path selection, Qt sandbox-disable override removed, installed-host tests parameterized via DSH_NODE_MODULES, author-local paths removed from docs, empty tool-reference template.

Portable edition 2026-10-29: rebuilt from project commit b1531d6 with the same bundled runtimes. Added the per-model tool allowlist (gateway/tool_filter.py), Arena rejection classification and one-time retry for explicitly rejected submissions, /api/me account identification, and a Chinese user manual (使用手册.md). Browser extension 1.6.0. The DSH receive-watchdog patch is not distributed.
