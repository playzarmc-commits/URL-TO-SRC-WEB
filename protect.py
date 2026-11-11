"""Builds the protected website script.

  python protect.py              # guard + packed
  python protect.py --no-guard   # packed only (use while debugging in DevTools)

frontend/guard.js + frontend/site.src.js  ➜  static/js/site.js

The source is UTF-8 encoded, XOR-ed with a fresh random key on every build and
base64-wrapped; the browser decodes and runs it. This hides the code from a
casual "view source" and stops simple copy-paste of your API calls. It is a
deterrent only — anything the browser runs can be recovered by a determined
person. The real protection is server-side: signed short-lived tokens, per-IP
limits and key-gated API (see app.py).
"""

import base64
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def build(guard=True):
    parts = []
    if guard:
        parts.append(open(os.path.join(ROOT, "frontend", "guard.js"), encoding="utf-8").read())
    parts.append(open(os.path.join(ROOT, "frontend", "site.src.js"), encoding="utf-8").read())
    data = "\n;\n".join(parts).encode("utf-8")
    key = secrets.token_bytes(16)
    packed = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    payload = base64.b64encode(packed).decode()
    k64 = base64.b64encode(key).decode()
    out = (
        "(function(){try{var p=\"%s\",k=atob(\"%s\"),a=atob(p),o=new Uint8Array(a.length);"
        "for(var i=0;i<a.length;i++)o[i]=a.charCodeAt(i)^k.charCodeAt(i%%k.length);"
        "(new Function(new TextDecoder().decode(o)))()}catch(e){}})();" % (payload, k64)
    )
    dest = os.path.join(ROOT, "static", "js", "site.js")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Built {dest} ({len(out)} bytes, guard={'on' if guard else 'off'})")


if __name__ == "__main__":
    build(guard="--no-guard" not in sys.argv)
