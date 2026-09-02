import socket, time
t0 = time.time()
s = socket.socket()
s.settimeout(0.05)
try:
    s.connect(("127.0.0.1", 5433))
    print("Connected in", round(time.time() - t0, 3), "s")
    s.close()
except Exception as e:
    print("Failed in", round(time.time() - t0, 3), "s:", e)
