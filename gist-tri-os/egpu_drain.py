import importlib.util, time, sys
spec = importlib.util.spec_from_file_location(
    "epc", "/Users/anush/github/claude-rocm-workspace/egpu-powercycle.py")
epc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(epc)  # safe: guarded by __main__

def cmd(c, timeout=15):
    t = epc.TelnetSession(epc.HOST, epc.PORT)
    try:
        t.read_until("User>"); t.send_line(epc.USER)
        t.read_until("Password>"); t.send_line(epc.PASSWORD)
        if not t.read_until("iBoot>"):
            print("login failed", file=sys.stderr); return False
        t.send_line(c)
        return t.read_until("Ok", timeout=timeout)
    finally:
        t.close()

print(f"[drain] outlet={epc.OUTLET} OFF"); 
assert cmd(f"set {epc.OUTLET} off"), "off not acked"
print("[drain] draining 15s (rail/cap discharge to reset stuck PSP)"); time.sleep(15)
print("[drain] outlet ON")
assert cmd(f"set {epc.OUTLET} on"), "on not acked"
print("[drain] waiting 40s for power-on + Thunderbolt re-enumerate"); time.sleep(40)
print("[drain] done")
