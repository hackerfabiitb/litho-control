"""Talk to the xyz1 firmware from a script.

ui/server.py owns COM4 while it runs, so scripts go through it (POST
/stage/send, and its /stage/events stream for the replies). When it isn't
running, the port is opened directly — which resets the Uno, so the link
waits for the firmware's READY before sending anything (bytes sent to the
bootloader are read as its commands).

    link = open_link()
    link.send("LS")
    line = link.wait_for(lambda l: l.startswith("LS "), timeout=2)
"""

import json
import queue
import threading
import time
import urllib.request

SERVER = "http://127.0.0.1:8765"


class Link:
    def __init__(self):
        self.lines = queue.Queue()
        self.description = ""

    def send(self, line):
        raise NotImplementedError

    def wait_for(self, predicate, timeout):
        """The next firmware line matching `predicate`; earlier lines that
        don't match are dropped. Raises TimeoutError."""
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                raise TimeoutError("no reply from the stage in time")
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                continue
            if predicate(line):
                return line

    def drain(self):
        while not self.lines.empty():
            self.lines.get_nowait()

    def close(self):
        pass


class ServerLink(Link):
    def __init__(self):
        super().__init__()
        status = json.load(urllib.request.urlopen(SERVER + "/stage/status", timeout=3))
        if not status["connected"]:
            raise RuntimeError("ui/server.py is running but the stage is not "
                               f"connected ({status.get('error')})")
        self.description = f"via ui/server.py ({status['port']})"
        self._stream = urllib.request.urlopen(SERVER + "/stage/events", timeout=None)
        self._ready = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()
        self._ready.wait(3)

    def _read(self):
        kind = None
        try:
            for raw in self._stream:
                line = raw.decode(errors="replace").rstrip("\r\n")
                if line.startswith("event: "):
                    kind = line[7:]
                elif line.startswith("data: ") and kind == "status":
                    self._ready.set()          # the stream is live from here on
                elif line.startswith("data: ") and kind == "line":
                    self.lines.put(line[6:])
        except OSError:
            pass                               # closed by close()

    def send(self, line):
        req = urllib.request.Request(
            SERVER + "/stage/send", data=json.dumps({"line": line}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5)

    def close(self):
        # The reader thread is blocked in readline() holding the stream's
        # lock, so close() would wait for the next keep-alive (up to 15 s).
        # Shutting the socket down wakes it immediately.
        try:
            import socket
            self._stream.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass


class SerialLink(Link):
    def __init__(self):
        super().__init__()
        import serial
        from find_arduino import BAUD, find_arduino
        port = find_arduino()
        self.description = f"direct on {port}"
        self._ser = serial.Serial(port, BAUD, timeout=0.2)
        threading.Thread(target=self._read, daemon=True).start()
        self.wait_for(lambda l: l == "READY", timeout=6)

    def _read(self):
        while self._ser.is_open:
            try:
                raw = self._ser.readline()
            except Exception:
                return
            line = raw.decode(errors="replace").strip()
            if line:
                self.lines.put(line)

    def send(self, line):
        self._ser.write((line + "\n").encode())

    def close(self):
        self._ser.close()


def open_link():
    try:
        urllib.request.urlopen(SERVER + "/stage/status", timeout=2)
    except OSError:
        return SerialLink()
    return ServerLink()
