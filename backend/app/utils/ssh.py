from __future__ import annotations

import base64
import binascii
import hashlib
import struct
from dataclasses import dataclass

ALLOWED_KEY_TYPES = {
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
}
MIN_RSA_BITS = 2048


class InvalidSshKey(ValueError):
    pass


@dataclass(frozen=True)
class ParsedSshKey:
    key_type: str
    blob_b64: str
    comment: str
    fingerprint: str  # "SHA256:..." giống `ssh-keygen -lf`

    @property
    def canonical(self) -> str:
        """Dạng lưu DB: '<type> <base64>' (bỏ comment và options)."""
        return f"{self.key_type} {self.blob_b64}"


def _read_string(data: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 4 > len(data):
        raise InvalidSshKey("Key bị cắt cụt")
    (length,) = struct.unpack(">I", data[offset:offset + 4])
    offset += 4
    if offset + length > len(data):
        raise InvalidSshKey("Key bị cắt cụt")
    return data[offset:offset + length], offset + length


def fingerprint_sha256(blob: bytes) -> str:
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def parse_public_key(text: str) -> ParsedSshKey:
    """Parse một dòng OpenSSH public key. Từ chối private key, options, loại key yếu."""
    text = (text or "").strip()
    if not text:
        raise InvalidSshKey("Public key trống")
    if "PRIVATE KEY" in text:
        raise InvalidSshKey("Đây là PRIVATE key — chỉ được dán PUBLIC key (.pub)")
    if "\n" in text or "\r" in text:
        raise InvalidSshKey("Chỉ dán đúng một dòng public key")
    parts = text.split(None, 2)
    if len(parts) < 2:
        raise InvalidSshKey("Định dạng không hợp lệ, cần '<type> <base64> [comment]'")
    key_type, b64 = parts[0], parts[1]
    comment = parts[2].strip() if len(parts) > 2 else ""
    if key_type not in ALLOWED_KEY_TYPES:
        raise InvalidSshKey(f"Loại key '{key_type}' không được hỗ trợ")
    try:
        blob = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidSshKey("Phần base64 của key không hợp lệ") from exc
    embedded_type, offset = _read_string(blob, 0)
    if embedded_type.decode("ascii", "replace") != key_type:
        raise InvalidSshKey("Loại key không khớp với nội dung key")
    if key_type == "ssh-rsa":
        _exponent, offset = _read_string(blob, offset)
        modulus, offset = _read_string(blob, offset)
        bits = len(modulus.lstrip(b"\x00")) * 8
        if bits < MIN_RSA_BITS:
            raise InvalidSshKey(f"RSA key quá yếu ({bits} bit), tối thiểu {MIN_RSA_BITS} bit")
    elif key_type == "ssh-ed25519":
        pub, offset = _read_string(blob, offset)
        if len(pub) != 32:
            raise InvalidSshKey("Ed25519 key không hợp lệ")
    if len(comment) > 255:
        comment = comment[:255]
    return ParsedSshKey(key_type=key_type, blob_b64=base64.b64encode(blob).decode("ascii"), comment=comment,
                        fingerprint=fingerprint_sha256(blob))
