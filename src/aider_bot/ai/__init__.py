"""AI integration layer.

Modules are organized by responsibility:
- ``aider``: invokes the aider CLI
- ``llm_client``: calls the remote LLM HTTP API
- ``structuring``: converts free-form model output into schema-based data
- ``output``: parses and renders markdown for GitLab
- ``review``: review-specific prompts, caching, and validation
"""
