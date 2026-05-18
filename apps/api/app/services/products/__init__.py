"""Product layer: each product module wraps cross-cutting services.

A *product* (e.g. ``closeout_iq``, ``heatshield``, ``wageguard``) is a
user-facing feature bundle that is gated by the organisation's
subscription.  Product modules live under ``app.services.products.<name>``
and compose lower-level shared services (magic links, pricing engine,
billing, etc.) into cohesive workflows.
"""
