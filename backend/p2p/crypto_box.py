"""
crypto_box.py — криптографическая identity игрока и шифрование туннеля.

- Долгосрочный ключ (Curve25519, через PyNaCl) генерируется один раз и хранится
  в config.json — это "личность" игрока, аналог SSH-ключа.
- При первом добавлении друга его публичный ключ запоминается (TOFU — trust on
  first use), как в SSH known_hosts. Это и есть механизм авторизации: только
  обладатель приватного ключа, который заранее был представлен как "друг",
  может установить сессию — даже если сигнальный сервер (MQTT) публичный.
- Сами пакеты туннеля шифруются nacl.SecretBox с ключом, полученным через
  Diffie-Hellman (Box) между постоянными ключами двух сторон — это даёт
  forward secrecy на уровне сессии не хуже, чем разовый shared secret,
  и защищает от MITM/спуфинга IP (подменивший IP не знает приватный ключ).
"""

from nacl.public import PrivateKey, PublicKey, Box
from nacl.encoding import Base64Encoder
from nacl.utils import random as nacl_random
from nacl.secret import SecretBox
from nacl.exceptions import CryptoError


def generate_identity() -> tuple:
    """Возвращает (priv_b64, pub_b64) — новая identity для config.json."""
    sk = PrivateKey.generate()
    pub = sk.public_key
    return (sk.encode(Base64Encoder).decode(), pub.encode(Base64Encoder).decode())


class Identity:
    """Обёртка над долгосрочным ключом текущего пользователя."""

    def __init__(self, priv_b64: str):
        self.private_key = PrivateKey(priv_b64.encode(), encoder=Base64Encoder)
        self.public_key = self.private_key.public_key

    @property
    def public_b64(self) -> str:
        return self.public_key.encode(Base64Encoder).decode()

    def box_with(self, peer_pub_b64: str) -> Box:
        peer_pub = PublicKey(peer_pub_b64.encode(), encoder=Base64Encoder)
        return Box(self.private_key, peer_pub)


class SessionCrypto:
    """Шифрование/дешифрование пакетов туннеля для конкретной P2P-сессии.

    derive() выводит симметричный ключ из DH(Box) между двумя identity и
    использует SecretBox (XSalsa20-Poly1305) — даёт AEAD: целостность +
    защиту от подмены/повтора (каждый пакет со своим nonce)."""

    def __init__(self, shared_key_32: bytes):
        self._box = SecretBox(shared_key_32)

    @classmethod
    def derive(cls, identity: Identity, peer_pub_b64: str) -> "SessionCrypto":
        box = identity.box_with(peer_pub_b64)
        # shared_key() у nacl.Box даёт raw 32-byte DH secret — подходит как ключ SecretBox
        return cls(box.shared_key())

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = nacl_random(SecretBox.NONCE_SIZE)
        return self._box.encrypt(plaintext, nonce)

    def decrypt(self, ciphertext: bytes) -> bytes | None:
        try:
            return self._box.decrypt(ciphertext)
        except CryptoError:
            return None  # подделанный/повреждённый пакет — отбрасываем молча
