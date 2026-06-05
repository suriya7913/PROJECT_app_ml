"""
legislation_downloader — UK Legislation API client package.

Modules
-------
constants    : API constants, search config, and seed acts
rate_limiter : AdaptiveRateLimiter + probe_rate_limit()
http_utils   : build_session(), get_bytes()
registry     : URIRegistry (thread-safe) + parse_atom_feed()
discovery    : Three-layer parallel URI discovery
downloader   : Parallel XML + effects downloading
manifest     : JSON manifest writer
"""
