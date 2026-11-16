"""A minimal FTP server over a local folder for own_install's FTP transport tests. It mimics DBI's quirks that matter
here: NLST ignores its path argument (lists the current directory), and RNTO onto an existing file can be refused
(`refuse_overwrite`). Passive mode only; one thread per control connection."""
import socket
import socketserver
import threading
from pathlib import Path


class FakeFtp:
    def __init__(self, root, user='switch', password='secret-pass', refuse_overwrite=True):
        self.root, self.user, self.password = Path(root), user, password
        self.refuse_overwrite = refuse_overwrite
        self.commands = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                outer._session(self)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = Server(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def _session(self, h):
        def say(line):
            h.wfile.write((line + '\r\n').encode())

        cwd, authed, user, pasv, rnfr = '/', False, None, None, None

        def local(path):
            p = path if path.startswith('/') else (cwd.rstrip('/') + '/' + path)
            parts = []
            for seg in p.split('/'):
                if seg in ('', '.'):
                    continue
                if seg == '..':
                    parts and parts.pop()
                else:
                    parts.append(seg)
            return '/' + '/'.join(parts), self.root.joinpath(*parts) if parts else self.root

        def data_conn():
            conn, _ = pasv.accept()
            pasv.close()
            return conn

        say('220 fake DBI FTP')
        while True:
            raw = h.rfile.readline()
            if not raw:
                return
            line = raw.decode().rstrip('\r\n')
            cmd, _, arg = line.partition(' ')
            cmd = cmd.upper()
            self.commands.append(cmd)
            if cmd == 'USER':
                user = arg
                say('331 password')
            elif cmd == 'PASS':
                authed = user == self.user and arg == self.password
                say('230 ok' if authed else '530 login incorrect')
            elif not authed:
                say('530 not logged in')
            elif cmd in ('TYPE', 'NOOP'):
                say('200 ok')
            elif cmd == 'PWD':
                say(f'257 "{cwd}"')
            elif cmd == 'CWD':
                vpath, lp = local(arg)
                if lp.is_dir():
                    cwd = vpath
                    say('250 ok')
                else:
                    say('550 no such directory')
            elif cmd == 'MKD':
                _, lp = local(arg)
                lp.mkdir()
                say(f'257 "{arg}"')
            elif cmd == 'PASV':
                pasv = socket.socket()
                pasv.bind(('127.0.0.1', 0))
                pasv.listen(1)
                port = pasv.getsockname()[1]
                say(f'227 Entering Passive Mode (127,0,0,1,{port >> 8},{port & 255})')
            elif cmd == 'NLST':  # DBI: the argument is ignored
                _, lp = local('.')
                say('150 listing')
                c = data_conn()
                c.sendall(''.join(p.name + '\r\n' for p in sorted(lp.iterdir())).encode())
                c.close()
                say('226 done')
            elif cmd == 'RETR':
                _, lp = local(arg)
                if not lp.is_file():
                    pasv.close()
                    say('550 not found')
                    continue
                say('150 sending')
                c = data_conn()
                c.sendall(lp.read_bytes())
                c.close()
                say('226 done')
            elif cmd == 'STOR':
                _, lp = local(arg)
                say('150 receiving')
                c = data_conn()
                chunks = []
                while True:
                    b = c.recv(65536)
                    if not b:
                        break
                    chunks.append(b)
                c.close()
                lp.write_bytes(b''.join(chunks))
                say('226 stored')
            elif cmd == 'DELE':
                _, lp = local(arg)
                if lp.is_file():
                    lp.unlink()
                    say('250 deleted')
                else:
                    say('550 not found')
            elif cmd == 'RNFR':
                _, rnfr = local(arg)
                say('350 ready' if rnfr.exists() else '550 not found')
            elif cmd == 'RNTO':
                _, lp = local(arg)
                if lp.exists() and self.refuse_overwrite:
                    say('550 file exists')
                else:
                    rnfr.replace(lp)
                    say('250 renamed')
            elif cmd == 'QUIT':
                say('221 bye')
                return
            else:
                say('502 not implemented')
