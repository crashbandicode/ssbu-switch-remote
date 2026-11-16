"""skyline_tcplog.py: waits for the logger to come up, records chunks with timestamps, survives a reconnect."""
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
import skyline_tcplog as st  # noqa: E402


class TcpLog(unittest.TestCase):
    def test_late_server_two_sessions(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]

        def server():
            time.sleep(0.8)  # the console's logger comes up after the reader started
            with socket.socket() as srv:
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(('127.0.0.1', port))
                srv.listen(1)
                for msg in (b'[PluginManager] Loaded \'libarcropolis.nro\'\n', b'[PluginManager] Running `main`\n'):
                    c, _ = srv.accept()
                    c.sendall(msg)
                    time.sleep(0.2)
                    c.close()
        threading.Thread(target=server, daemon=True).start()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'log'
            stats = st.run('127.0.0.1', port, out, seconds=3.0, retry_s=0.1)
            text = out.read_text()
        self.assertEqual(stats['connects'], 2)
        self.assertIn("Loaded 'libarcropolis.nro'", text)
        self.assertIn('Running `main`', text)
        self.assertGreaterEqual(text.count('### '), 6)


if __name__ == '__main__':
    unittest.main()
