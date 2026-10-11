#!/usr/bin/env python3
"""Create a private starting env file; never overwrite an existing one."""
import argparse
import os
from pathlib import Path
import secrets

parser = argparse.ArgumentParser()
parser.add_argument("--template", default=".env.example")
parser.add_argument("--output", default=".env")
parser.add_argument("--new-marzban", action="store_true", help="Generate sudo password for a NEW Marzban install")
args = parser.parse_args()
source = Path(args.template).read_text()
for placeholder in ("REPLACE_WITH_RANDOM_32PLUS_BYTES", "REPLACE_WITH_DIFFERENT_RANDOM_32PLUS_BYTES"):
    source = source.replace(placeholder, secrets.token_urlsafe(48))
if args.new_marzban:
    source = source.replace("MARZBAN_SUDO_PASSWORD=REPLACE_ME", "MARZBAN_SUDO_PASSWORD=" + secrets.token_urlsafe(48))
fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as output:
    output.write(source)
print(f"Created {args.output}; edit the Marzban/provider credentials before starting.")
