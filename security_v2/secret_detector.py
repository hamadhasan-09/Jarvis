"""
Secret Detector
Prevents JARVIS from storing sensitive data in memory.
"""

import re


class SecretDetector:
    """Detect and block storage of secrets."""

    # Patterns that indicate secrets
    PATTERNS = [
        # Credit cards (13-19 digits, may have spaces/dashes)
        (r'\b(?:\d[ -]*?){13,19}\b', "credit_card"),

        # Common password phrases
        (r'(?i)(?:my\s+password\s+is|password\s*[:=]\s*)\S+', "password"),
        (r'(?i)(?:my\s+pin\s+is|pin\s*[:=]\s*)\d{3,8}', "pin"),

        # API keys (common formats)
        (r'sk-[a-zA-Z0-9]{20,}', "openai_key"),
        (r'sk-ant-[a-zA-Z0-9\-_]{20,}', "anthropic_key"),
        (r'gsk_[a-zA-Z0-9]{20,}', "groq_key"),
        (r'AIza[a-zA-Z0-9\-_]{35}', "google_key"),

        # Bank/account numbers
        (r'(?i)(?:account\s+(?:number|no)|iban)\s*[:=#]?\s*\d{8,}', "bank_account"),

        # Social Security / CNIC
        (r'\b\d{5}-\d{7}-\d\b', "cnic"),  # Pakistan CNIC
        (r'\b\d{3}-\d{2}-\d{4}\b', "ssn"),  # US SSN

        # OTP / verification codes
        (r'(?i)(?:otp|verification\s+code|code\s+is)\s*[:=]?\s*\d{4,8}', "otp"),

        # Private keys
        (r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----', "private_key"),
    ]

    def __init__(self):
        self.compiled = [(re.compile(p), name) for p, name in self.PATTERNS]

    def check(self, text: str) -> dict:
        """
        Check if text contains secrets.
        Returns: {"safe": bool, "detected": [list of secret types], "reason": str}
        """
        if not text:
            return {"safe": True, "detected": [], "reason": ""}

        detected = []
        for pattern, name in self.compiled:
            if pattern.search(text):
                detected.append(name)

        if detected:
            return {
                "safe": False,
                "detected": detected,
                "reason": f"Refusing to store — detected: {', '.join(detected)}"
            }

        return {"safe": True, "detected": [], "reason": ""}

    def redact(self, text: str) -> str:
        """Replace detected secrets with [REDACTED]."""
        if not text:
            return text
        clean = text
        for pattern, name in self.compiled:
            clean = pattern.sub(f"[REDACTED_{name.upper()}]", clean)
        return clean