#!/usr/bin/env python3
"""Sign a pre-validated billing event and send it to the private addon API."""
import argparse
import hashlib
import hmac
import os
import time
import urllib.request
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("json_file")
p.add_argument("--url", default="http://127.0.0.1:8787")
a = p.parse_args()
secret = os.environ["RESIP_BILLING_HMAC_KEY"]
body = Path(a.json_file).read_bytes()
ts = str(int(time.time()))
sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
req = urllib.request.Request(a.url+"/v1/billing/events", body,
                             {"Content-Type": "application/json", "X-Resip-Timestamp": ts,
                              "X-Resip-Signature": sig}, method="POST")
with urllib.request.urlopen(req, timeout=40) as r:
    print(r.read().decode())
