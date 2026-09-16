"""No models.

OpenRouter keys used to live here, as a rotation queue. They moved to
llm-gateway, which holds them for every service that needs one — so this app
stores no provider credential at all, and there is no second place to put one.

The table is dropped in migration 0003.
"""
