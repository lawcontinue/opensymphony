# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-06-13

### Security
- Path traversal protection in tools and workshop (prevent `../` escapes)
- Registry overwrite protection (prevent replacing core tools)
- Workshop sandbox isolation (restrict file I/O scope)
- Context isolation between agent sessions
- FanOut serialization safety (validate all inputs)
- Error message sanitization (no internal path/stack leaks)
- 32 blocked patterns for dangerous operations
- 37 dedicated security tests added

### Added
- Anthropic provider support (Claude models)
- Enhanced LLM Router with multi-provider failover
- Tool execution middleware with allow/deny hooks
- Production-grade tool safety layer

### Changed
- Agent tool execution now goes through safety middleware
- HTTP gateway error responses sanitized
- Kernel initialization hardened against injection

### Tests
- 257 tests passing (up from 377 in 0.1.0 — restructured suite)

---

## [0.1.0] - 2026-05-30

### Added
- Soul system with 13 built-in YAML personas and Soul Compiler
- Governance layer: voting, precedent, defense, human-in-the-loop
- Tool Workshop: agents can create and deploy tools at runtime
- Three-tier memory (L1 in-memory, L2 SQLite, L3 cloud)
- Declarative pipeline engine with retry and fallback
- HTTP gateway with SSE streaming
- Intent Bridge: natural language to structured intents
- LLM Router with multi-provider support (cloud + local)
- Agent pool, scheduler, and sandbox
- Novel pipeline and content factory apps
- 377 tests passing
