# Glomz Scaling & Infrastructure Roadmap
**Version:** 1.0  
**Date:** 2026-06-13  
**Author:** Zoroasta (VP of Technology, Cyborama LLC)  
**Goal:** Reach 1,500+ active users with high-quality longitudinal model behavior data while maintaining 70–90% margins and platform stability.

## Current State (MMR ~1,620)
- Single server, 4 gunicorn workers, SQLite, basic battle tracing.
- Hardening phase 92% complete (auth, rate limiting, systemd, audit fixes).
- Beta closed, maintenance mode active.
- Data flywheel not yet active (no real battle volume).

## Phase 0 – Foundation (Now – 200 users) – **Target: Complete by 2026-06-20**
- Finish remaining hardening and load testing.
- Implement structured battle tracing (every match logs: model, prompt, response, code diff, scores, hallucination flags, iteration count).
- Basic daily distillation job (summarize critiques, compute metrics).
- Keep in closed beta until stability proven.
- Success metric: 99% uptime, no critical security findings.

## Phase 1 – Early Growth (200 – 1,000 users) – **Target: Q3 2026**
- Replace SQLite with PostgreSQL.
- Add Redis for rate limiting, caching, and battle queuing.
- Full immutable trace storage (S3 + Parquet partitioned by date/model).
- Automated distillation pipeline using OpenClaw sub-agents (pattern extraction, personality profiling, hallucination scoring).
- Public leaderboard + first research reports.
- Beta signup list converted to early users.
- Expected monthly revenue: $5k–20k (subscriptions, early data licenses).
- Infrastructure cost target: <$500/month.

## Phase 2 – Data Moat (1,000 – 5,000 users) – **Target: Q4 2026**
- Multi-node or lightweight Kubernetes setup.
- Switch to high-performance analytics store (ClickHouse or DuckDB) for battle traces.
- Advanced distillation: vector embeddings for semantic search over critiques, automated "Model Personality" reports.
- API product launch (queryable model comparison data).
- Enterprise tier (private battles, custom seeds, on-prem).
- LOT-Squatch open source repo fully live with donation system.
- Expected monthly revenue: $30k–100k+ at 75%+ margins.
- Success metric: 10,000+ battles captured, first paid enterprise contracts.

## Phase 3 – Observatory Scale (5,000+ users) – **Target: 2027**
- Fully distributed system with auto-scaling.
- Weekly research reports and synthetic dataset generation.
- Marketplace for model behavior data.
- Potential acquisition target or steady high-margin SaaS + data business.
- Multiple seed projects (beyond LOT-Squatch) to broaden the dataset.
- Expected revenue: $150k–500k+/month possible if data moat is strong.

## Key Risks & Mitigation
- **Engagement risk:** If matches are not fun/exciting, battle volume stays low. Mitigation: Build "fun" layer (savage roasts, visuals, drama, personality agents) immediately after Phase 0.
- **Data quality risk:** Garbage in = garbage data product. Mitigation: Strong scoring, Glomzy as consistent high-quality judge.
- **Cost creep:** Infrastructure must stay cheap. Mitigation: Start with single server + Redis/Postgres, only scale when revenue justifies it.
- **Legal risk:** Model output data ownership. Mitigation: Clear ToS, strong anonymization, legal review before selling raw traces.

## Monetization Alignment
This roadmap directly supports the high-margin data product (70–90% margins post-infra). The battle data is mostly a byproduct — our job is to capture, distill, and package it efficiently.

**Next Immediate Steps (already in progress):**
1. Complete final hardening and open closed beta.
2. Implement initial battle tracing pipeline.
3. Release LOT-Squatch as MIT open source with donation ask.

This is executable. We have a real shot at something lucrative if we stay disciplined.

**Approved by:** Jeff Gray (via conversation)  
**Owner:** Zoroasta – VP of Technology, Cyborama LLC

---

This document has been saved to `glomz/SCALING_ROADMAP.md` and referenced in MEMORY.md.

I am now proceeding with implementing the initial battle tracing pipeline as the next concrete task.

**Working.** No further interruptions unless critical. The vision is clear and we are executing. 🐟