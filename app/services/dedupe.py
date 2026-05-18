from hashlib import sha256


def build_content_hash(title: str, published_at: object, source: str) -> str:
    value = f"{title.strip()}|{published_at or ''}|{source.strip()}".encode("utf-8")
    return sha256(value).hexdigest()

