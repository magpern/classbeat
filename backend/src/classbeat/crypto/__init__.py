"""Envelope encryption (``envelope``, pure) and the key-encryption keys (``keks``), ADR 0004 §2.

Only ``classbeat.connections``, ``classbeat.auth`` (TOTP) and ``classbeat.crypto.keks`` import
``classbeat.crypto.envelope``; ``tests/test_crypto_guard.py`` enforces the list.
"""
