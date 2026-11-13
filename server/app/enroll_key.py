"""Short, human-typeable enrollment keys.

A key is the *only* thing a device needs. The agent .exe has the server address
baked in at build time, so whoever enrols a PC types `TSSM-4K7P-9XQ2` and nothing
else -- no URL, no QR code, no link.

The body uses the Crockford base32 alphabet (no I, L, O or U), which means the
characters people actually mistype -- 1/I/L and 0/O -- are never ambiguous and can
be corrected on input instead of rejected.
"""
import secrets

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PREFIX = "TSSM"
BODY_LEN = 8

# Substitutions that can only ever be a typo, because the left-hand character is
# not in ALPHABET and so can never appear in a real key.
CONFUSABLES = str.maketrans({"O": "0", "I": "1", "L": "1", "U": "V"})


def generate_key() -> str:
    """A fresh key in canonical form, e.g. `TSSM-4K7P-9XQ2`."""
    body = "".join(secrets.choice(ALPHABET) for _ in range(BODY_LEN))
    return f"{PREFIX}-{body[:4]}-{body[4:]}"


def normalize_key(raw: str | None) -> str | None:
    """Canonical `TSSM-XXXX-XXXX` form of whatever the user typed, or None.

    Forgiving about everything that cannot change which key was meant: case,
    spaces, dashes, a missing prefix, and the 0/O and 1/I/L confusions. Strict
    about anything else, so a wrong key fails fast rather than hitting the DB.
    """
    if not raw:
        return None
    s = "".join(c for c in raw.upper() if c.isalnum()).translate(CONFUSABLES)
    # Only strip the prefix when the length says it really is one, so a key typed
    # without its prefix whose body happens to start with "TSSM" still works.
    if len(s) == len(PREFIX) + BODY_LEN and s.startswith(PREFIX):
        s = s[len(PREFIX):]
    if len(s) != BODY_LEN or any(c not in ALPHABET for c in s):
        return None
    return f"{PREFIX}-{s[:4]}-{s[4:]}"
