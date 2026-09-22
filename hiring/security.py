import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qsl
import jwt


def hash_password(value):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode(), salt.encode(), 600000).hex()
    return f"{salt}:{digest}"


def check_password(value, stored):
    if ":" not in stored:
        return False
    salt, expected = stored.split(":", 1)
    actual = hashlib.pbkdf2_hmac("sha256", value.encode(), salt.encode(), 600000).hex()
    return hmac.compare_digest(expected, actual)


def token_for(user, secret):
    return jwt.encode({"sub": user.id, "iat": int(time.time()), "exp": int(time.time()) + 43200, "aud": "rezumit-hiring"}, secret, algorithm="HS256")


def max_identity(data, token):
    pairs = parse_qsl(data, keep_blank_values=True, strict_parsing=True)
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("Duplicate launch fields")
    fields = dict(pairs)
    received = fields.pop("hash", "")
    key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    message = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    expected = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    if not token or not hmac.compare_digest(received, expected):
        raise ValueError("Invalid MAX signature")
    age = time.time() - int(fields.get("auth_date", 0))
    if not -30 <= age <= 3600:
        raise ValueError("Expired MAX launch data")
    user = json.loads(fields["user"])
    if not isinstance(user, dict):
        raise ValueError("Invalid MAX user")
    identifier = user.get("id", user.get("user_id"))
    if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier <= 0:
        raise ValueError("Invalid MAX user")
    name = user.get("first_name") or user.get("name") or "Кандидат"
    if not isinstance(name, str):
        raise ValueError("Invalid MAX name")
    return str(identifier), name
