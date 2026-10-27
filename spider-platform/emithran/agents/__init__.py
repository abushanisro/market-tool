"""Emithran Brain: specialist agents, an orchestrator and a provider-agnostic LLM gateway.

Design rules (see docs/AGENTS.md):
  * Every number in a finding comes from deterministic code over the stored data and is listed as evidence.
  * An LLM (optional) only writes opinions about those findings; opinions citing evidence that does not exist are dropped.
  * Internal (taxonomy) data is only sent to local models unless the operator explicitly allows hosted providers.
"""
