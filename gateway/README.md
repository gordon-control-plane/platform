# Gateway (LiteLLM + Shunt)

The `gateway` module provides the central LLM routing and intelligence proxy for the platform.

## Overview
This module acts as the interface between the agent execution planes (Automation and Interactive) and the foundational models. It is built on **LiteLLM** to provide vendor-agnostic routing, augmented by a custom **Shunt Middleware**.

## Shunt Middleware
The Shunt is a token-optimizing middleware designed to intercept, cache, route, and enforce policy on LLM calls without introducing blocking latency into the event loop.

### Speculative Fork Note
There is an ongoing architectural decision regarding the Shunt implementation:
- **Option A:** An in-process Python middleware leveraging LiteLLM's internal state.
- **Option B:** A standalone, high-performance Reverse Proxy (e.g., Rust/Go) placed in front of LiteLLM for maximum streaming throughput.

Currently, the implementation must focus on non-blocking, safe parsing of headers and telemetry, and preventing injection attacks by avoiding dynamic template evaluation.

## Security
- **Injection Defenses:** Uses safe serialization exclusively.
- **Cross-Tenant Isolation:** State and caches are strictly keyed by tenant/agent IDs.
