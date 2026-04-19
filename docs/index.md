# VORTEX Trading System — Documentation Index

This directory contains the complete technical documentation for the VORTEX Trading System v2.0.0.

## Core Architecture

| Document | Description |
| :--- | :--- |
| [TECHNICAL_DOCUMENTATION.md](TECHNICAL_DOCUMENTATION.md) | Full system architecture whitepaper: three-tier design (perception, decision, execution), module dependency graph, and data flow diagrams. |
| [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) | Developer onboarding guide: environment setup, coding standards, testing workflow, and contribution guidelines. |

## Trading Engine Modules

| Document | Description |
| :--- | :--- |
| [CHANLUN_INTEGRATION.md](CHANLUN_INTEGRATION.md) | Mathematical model of the Chanlun (缠论) engine: bi/stroke/segment detection algorithm, divergence energy calculation, and pivot classification. |
| [ICEBERG_EXECUTION.md](ICEBERG_EXECUTION.md) | Iceberg order execution algorithm: child order sizing, randomization strategy, ATR-aware slippage model, and TWAP scheduling. |
| [MULTIMODAL_INTEGRATION.md](MULTIMODAL_INTEGRATION.md) | Multimodal Oracle integration: Fear & Greed Index, news sentiment fusion, 5-minute TTL async cache, and graceful degradation design. |
| [BINANCE_DATA_INTEGRATION.md](BINANCE_DATA_INTEGRATION.md) | Binance WebSocket and REST API integration: market data normalization, order book depth management, and reconnection strategy. |

## Optimization & Reports

| Document | Description |
| :--- | :--- |
| [OPTIMIZATION_REPORT.md](OPTIMIZATION_REPORT.md) | V2.0 institution-grade optimization report: 746 code style fixes, arbitrage engine VWAP rebuild, signal engine logic repair, and performance benchmark results. |
| [VORTEX_V4_TOP_INSTITUTIONAL_AUDIT.md](VORTEX_V4_TOP_INSTITUTIONAL_AUDIT.md) | Full-system audit report: security scan, consistency review, usability validation, and performance benchmarks. |
| [VORTEX_V4_ULTIMATE_REPORT.md](VORTEX_V4_ULTIMATE_REPORT.md) | V4 ultimate feature summary: AI self-evolution, cross-platform arbitrage, and multi-modal macro filtering. |

## Roadmap

| Document | Description |
| :--- | :--- |
| [PHASE2_FEATURE_BLUEPRINT.md](PHASE2_FEATURE_BLUEPRINT.md) | Phase 2 feature roadmap: on-chain data integration, options Greeks hedging, and multi-strategy portfolio allocation. |
| [PROJECT_LEVIATHAN_SUMMARY.md](PROJECT_LEVIATHAN_SUMMARY.md) | Project Leviathan summary: long-term vision for decentralized quantitative infrastructure. |
