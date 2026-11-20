"""Settings from the environment (`settings.py`), application constants and enums (`constants.py`), the shared
secret-file reader with `StartRefusedError` (`secret_files.py`) and the values built on webauth's types
(`web_policy.py`).

The client secret, the session signing key and the TURN key files are read at start from the files the settings name
and never enter the environment; a wrong setting is refused by its name alone.
"""
