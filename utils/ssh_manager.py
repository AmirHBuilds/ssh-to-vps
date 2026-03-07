"""
SSH Connection Manager - Handles SSH connections with full PTY support
"""
import paramiko
import threading
import time
import io
import os
import logging
import socket
from typing import Optional, Callable

logger = logging.getLogger(__name__)

KEEPALIVE_INTERVAL = int(os.getenv("SSH_KEEPALIVE_INTERVAL", "30"))


class SSHConnectionError(Exception):
    pass


class SSHConnection:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: Optional[str] = None,
        private_key: Optional[str] = None,
        key_passphrase: Optional[str] = None,
        auth_type: str = "password",
        keep_alive: bool = True,
        on_disconnect: Optional[Callable] = None,
        on_output: Optional[Callable] = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.private_key = private_key
        self.key_passphrase = key_passphrase
        self.auth_type = auth_type
        self.keep_alive = keep_alive
        self.on_disconnect = on_disconnect
        self.on_output = on_output

        self.client: Optional[paramiko.SSHClient] = None
        self.channel: Optional[paramiko.Channel] = None
        self.shell = None
        self._connected = False
        self._lock = threading.Lock()
        self._output_buffer = ""
        self._output_thread: Optional[threading.Thread] = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def connect(self) -> bool:
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                "hostname": self.host,
                "port": self.port,
                "username": self.username,
                "timeout": 15,
                "banner_timeout": 15,
                "auth_timeout": 15,
            }

            if self.auth_type == "password":
                connect_kwargs["password"] = self.password

            elif self.auth_type in ("key", "key_passphrase"):
                if self.private_key:
                    key_file = io.StringIO(self.private_key)
                    pkey = None
                    passphrase = self.key_passphrase if self.auth_type == "key_passphrase" else None

                    for key_class in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey]:
                        try:
                            pkey = key_class.from_private_key(key_file, password=passphrase)
                            break
                        except Exception:
                            key_file.seek(0)
                            continue

                    if not pkey:
                        raise SSHConnectionError("❌ Could not parse private key. Check format and passphrase.")

                    connect_kwargs["pkey"] = pkey
                else:
                    raise SSHConnectionError("❌ Private key not provided.")

            self.client.connect(**connect_kwargs)

            # Open interactive shell
            self.channel = self.client.invoke_shell(term="xterm", width=220, height=50)
            self.channel.settimeout(0.5)
            self._connected = True

            # Start output reader thread
            self._stop_event.clear()
            self._output_thread = threading.Thread(target=self._read_output, daemon=True)
            self._output_thread.start()

            # Start keepalive thread if enabled
            if self.keep_alive:
                self._keepalive_thread = threading.Thread(target=self._send_keepalive, daemon=True)
                self._keepalive_thread.start()

            # Wait for initial prompt
            time.sleep(1.5)
            return True

        except paramiko.AuthenticationException as e:
            raise SSHConnectionError(f"🔐 Authentication failed: {str(e)}")
        except paramiko.SSHException as e:
            raise SSHConnectionError(f"🔌 SSH error: {str(e)}")
        except socket.timeout:
            raise SSHConnectionError(f"⏱️ Connection timed out to {self.host}:{self.port}")
        except ConnectionRefusedError:
            raise SSHConnectionError(f"🚫 Connection refused at {self.host}:{self.port}")
        except Exception as e:
            raise SSHConnectionError(f"❌ Connection failed: {str(e)}")

    def send_command(self, command: str) -> None:
        if not self._connected or not self.channel:
            raise SSHConnectionError("Not connected")

        with self._lock:
            self.channel.send(command + "\n")

    def send_input(self, data: str) -> None:
        """Send raw input (for interactive prompts)"""
        if not self._connected or not self.channel:
            raise SSHConnectionError("Not connected")
        with self._lock:
            self.channel.send(data + "\n")

    def send_control(self, key: str) -> None:
        """Send a control character (e.g. Ctrl+X => \x18)."""
        if not self._connected or not self.channel:
            raise SSHConnectionError("Not connected")

        if not key or len(key) != 1 or not key.isalpha():
            raise SSHConnectionError("Control key must be a single alphabet letter")

        ctrl_char = chr(ord(key.upper()) - 64)
        with self._lock:
            self.channel.send(ctrl_char)

    def _read_output(self):
        buffer = ""
        last_send_time = time.time()
        FLUSH_INTERVAL = 0.8  # seconds to wait before sending partial output
        MAX_BUFFER = int(os.getenv("MAX_OUTPUT_LENGTH", "3500"))

        while not self._stop_event.is_set():
            try:
                if self.channel.recv_ready():
                    chunk = self.channel.recv(4096).decode("utf-8", errors="replace")
                    buffer += chunk
                    last_send_time = time.time()
                else:
                    # Flush buffer if we have data and waited long enough
                    if buffer and (time.time() - last_send_time > FLUSH_INTERVAL):
                        cleaned = self._clean_ansi(buffer)
                        if cleaned.strip():
                            # Split large outputs
                            while len(cleaned) > MAX_BUFFER:
                                part = cleaned[:MAX_BUFFER]
                                if self.on_output:
                                    self.on_output(part, False)
                                cleaned = cleaned[MAX_BUFFER:]
                            if self.on_output:
                                self.on_output(cleaned, False)
                        buffer = ""

                    # Check if channel closed
                    if self.channel.exit_status_ready() and not self.channel.recv_ready():
                        time.sleep(0.5)
                        if not self.channel.recv_ready():
                            self._handle_disconnect("🔌 Remote server closed the connection.")
                            break

                    time.sleep(0.05)

            except Exception as e:
                if self._connected:
                    logger.error(f"Output read error: {e}")
                    self._handle_disconnect(f"⚠️ Connection lost: {str(e)}")
                break

    def _clean_ansi(self, text: str) -> str:
        """Remove ANSI escape codes"""
        import re
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        text = ansi_escape.sub('', text)
        # Remove other control characters except newline and tab
        text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', text)
        return text

    def _send_keepalive(self):
        while not self._stop_event.is_set() and self._connected:
            try:
                self._stop_event.wait(KEEPALIVE_INTERVAL)
                if self._connected and self.client and self.client.get_transport():
                    transport = self.client.get_transport()
                    if transport and transport.is_active():
                        transport.send_ignore()
            except Exception as e:
                logger.debug(f"Keepalive error: {e}")

    def _handle_disconnect(self, reason: str = "Disconnected"):
        if self._connected:
            self._connected = False
            self._stop_event.set()
            if self.on_disconnect:
                self.on_disconnect(reason)

    def disconnect(self, reason: str = "User disconnected"):
        self._stop_event.set()
        self._connected = False
        try:
            if self.channel:
                self.channel.close()
            if self.client:
                self.client.close()
        except Exception:
            pass
        if self.on_disconnect:
            self.on_disconnect(reason)

    @property
    def is_connected(self) -> bool:
        return self._connected


# Global session store: { telegram_user_id: SSHConnection }
_active_connections: dict[int, SSHConnection] = {}
_connections_lock = threading.Lock()


def store_connection(user_id: int, conn: SSHConnection):
    with _connections_lock:
        # Disconnect existing if any
        if user_id in _active_connections:
            try:
                _active_connections[user_id].disconnect("Replaced by new connection")
            except Exception:
                pass
        _active_connections[user_id] = conn


def get_connection(user_id: int) -> Optional[SSHConnection]:
    with _connections_lock:
        return _active_connections.get(user_id)


def remove_connection(user_id: int):
    with _connections_lock:
        if user_id in _active_connections:
            del _active_connections[user_id]


def get_active_count() -> int:
    with _connections_lock:
        return len(_active_connections)
