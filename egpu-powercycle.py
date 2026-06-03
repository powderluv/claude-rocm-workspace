#!/usr/bin/env python3
"""Power-cycle the eGPU via the iBoot G2 network power switch (telnet).

Replaces the manual "physically replug the eGPU" step: cutting and restoring
power to the Razer Core X enclosure re-enumerates the GPU over Thunderbolt the
same way a replug does. Run this, then re-run the phase-9 bring-up.

Uses a raw socket with minimal telnet IAC handling (no telnetlib / no telnet
binary, both of which are gone from recent Python / macOS).

Config via env (defaults match the attached iBoot G2):
  IBOOT_HOST=10.10.10.10  IBOOT_PORT=23  IBOOT_USER=admin  IBOOT_PASSWORD=...
  IBOOT_OUTLET=outlet     IBOOT_WAIT=20   (seconds to wait after the cycle)

Exit 0 only if the controller acknowledged the cycle ("Ok").
"""
import os
import socket
import sys
import time

HOST = os.environ.get("IBOOT_HOST", "10.10.10.10")
PORT = int(os.environ.get("IBOOT_PORT", "23"))
USER = os.environ.get("IBOOT_USER", "admin")


def _iboot_password():
    """Password from $IBOOT_PASSWORD, or a local gitignored egpu-iboot.env
    (keeps the credential out of git)."""
    pw = os.environ.get("IBOOT_PASSWORD")
    if pw:
        return pw
    envf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "egpu-iboot.env")
    try:
        for line in open(envf):
            line = line.strip()
            if line.startswith("IBOOT_PASSWORD="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


PASSWORD = _iboot_password()
OUTLET = os.environ.get("IBOOT_OUTLET", "outlet")
WAIT_S = int(os.environ.get("IBOOT_WAIT", "20"))

IAC, DONT, DO, WONT, WILL, SB, SE = 0xFF, 0xFE, 0xFD, 0xFC, 0xFB, 0xFA, 0xF0


class TelnetSession:
    """Minimal telnet client: refuses all options, surfaces the plain text."""

    def __init__(self, host, port, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._raw = bytearray()      # bytes received, IAC sequences not yet consumed
        self.text = bytearray()      # decoded application text seen so far

    def _process(self):
        """Consume complete IAC sequences from self._raw, append text to self.text.
        Leaves an incomplete trailing IAC sequence in self._raw for the next read."""
        out = bytearray()
        i = 0
        n = len(self._raw)
        while i < n:
            b = self._raw[i]
            if b != IAC:
                out.append(b)
                i += 1
                continue
            if i + 1 >= n:                       # incomplete: keep from here
                break
            cmd = self._raw[i + 1]
            if cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= n:                   # incomplete option
                    break
                opt = self._raw[i + 2]
                # Standard client behavior: accept the server's ECHO(1)/SGA(3)
                # (refusing ECHO makes the iBoot reset the session); we won't
                # enable any option ourselves.
                if cmd == WILL:
                    resp = DO if opt in (1, 3) else DONT
                elif cmd == DO:
                    resp = WONT
                else:
                    resp = None                  # DONT/WONT: nothing to answer
                if resp is not None:
                    self.sock.sendall(bytes([IAC, resp, opt]))
                i += 3
            elif cmd == SB:                      # subnegotiation: skip to IAC SE
                j = i + 2
                while j + 1 < n and not (self._raw[j] == IAC and self._raw[j + 1] == SE):
                    j += 1
                if j + 1 >= n:                   # incomplete
                    break
                i = j + 2
            else:                                # 2-byte command (NOP/GA/etc.)
                i += 2
        del self._raw[:i]
        if out:
            self.text.extend(out)
            sys.stdout.write(out.decode("latin-1", "replace"))
            sys.stdout.flush()

    def read_until(self, token, timeout=20.0):
        token_b = token.encode()
        start_len = 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.sock.settimeout(max(0.1, deadline - time.time()))
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            self._raw.extend(chunk)
            self._process()
            if token_b in self.text[max(0, start_len - len(token_b)):]:
                return True
        return token_b in self.text

    def send_line(self, line):
        self.sock.sendall(line.encode() + b"\r\n")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _attempt():
    """One login + cycle attempt. Returns True if the cycle was acknowledged.
    Raises OSError (e.g. ConnectionReset) so the caller can retry."""
    t = TelnetSession(HOST, PORT)
    try:
        t.read_until("User>")
        t.send_line(USER)
        t.read_until("Password>")
        t.send_line(PASSWORD)
        if not t.read_until("iBoot>"):
            print("\n[egpu-powercycle] never reached iBoot> prompt (login failed?)",
                  file=sys.stderr)
            return False
        t.send_line(f"get {OUTLET}")
        t.read_until("iBoot>")
        t.send_line(f"set {OUTLET} cycle")
        return t.read_until("Ok", timeout=15)
    finally:
        t.close()


def main():
    if not PASSWORD:
        print("[egpu-powercycle] no password: set IBOOT_PASSWORD or create "
              "egpu-iboot.env (IBOOT_PASSWORD=...) next to this script",
              file=sys.stderr)
        return 2
    print(f"[egpu-powercycle] iBoot {HOST}:{PORT} (outlet={OUTLET})")
    attempts = int(os.environ.get("IBOOT_RETRIES", "4"))
    got_ok = False
    for n in range(1, attempts + 1):
        try:
            got_ok = _attempt()
            if got_ok:
                break
        except OSError as e:
            print(f"\n[egpu-powercycle] attempt {n}/{attempts} failed ({e}); "
                  f"retrying (iBoot allows one session — a stale one may reset us)",
                  file=sys.stderr)
            time.sleep(2)  # let the single iBoot session free up

    if not got_ok:
        print("\n[egpu-powercycle] could not cycle the outlet — check transcript",
              file=sys.stderr)
        return 1

    print(f"\n[egpu-powercycle] outlet cycle acknowledged; "
          f"waiting {WAIT_S}s for power-on + Thunderbolt re-enumerate")
    time.sleep(WAIT_S)
    print("[egpu-powercycle] done — eGPU should be re-enumerated; run phase-9 next")
    return 0


if __name__ == "__main__":
    sys.exit(main())
