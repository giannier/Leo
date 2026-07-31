"""Ports & Adapters for third-party integrations.

Only code under this package may call third-party SDKs directly. Interfaces
(`LLMProvider`, `WhatsAppProvider`, `PaymentProvider`, `EmailProvider`,
`StorageProvider`) are defined per-subpackage and must not be bypassed.
"""
