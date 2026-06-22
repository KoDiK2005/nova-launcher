"""
p2p — замена Radmin/Hamachi: NAT traversal + шифрованный туннель для Minecraft.

Состав:
  stun.py        — STUN-клиент (RFC 5389), определение типа NAT и публичного IP:port
  crypto_box.py  — идентичность игрока (Curve25519) + шифрование туннеля (XChaCha20-Poly1305)
  reliable_udp.py— надёжный канал поверх UDP (seq/ack, retransmit, keepalive)
  signaling.py   — обмен ICE-кандидатами и ключами между друзьями через relay (MQTT)
  punch.py       — UDP hole punching (simultaneous open) с ICE-подобным выбором кандидата
  turn_relay.py  — relay-фолбэк (TURN-lite) на случай двойного Symmetric NAT
  tunnel.py      — TCP(Minecraft) <-> зашифрованный UDP-туннель, реконнект, локальный IP
  manager.py     — публичное API верхнего уровня, используется из backend/api.py
"""
