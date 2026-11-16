"""
Tạo file cấu hình Dynamic Security cho Mosquitto với 1 tài khoản quản trị là backend.
Chạy 1 lần trước `docker compose up` (không ghi đè nếu file đã có, trừ khi --force).

    python init_mqtt.py [--password <mật khẩu backend>] [--force]

Mật khẩu phải trùng EDGE_MQTT_PASSWORD của server biên (mặc định: backend-secret).
"""
import argparse
import base64
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

OUT = Path(__file__).parent / "mosquitto" / "data" / "dynamic-security.json"


def hash_password(password: str, iterations: int = 101):
    salt = os.urandom(12)
    dk = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, iterations, dklen=64)
    return base64.b64encode(salt).decode(), base64.b64encode(dk).decode(), iterations


def acl(acltype, topic):
    return {"acltype": acltype, "topic": topic, "priority": 0, "allow": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", default=os.getenv("EDGE_MQTT_PASSWORD", "backend-secret"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if OUT.exists() and not args.force:
        print(f"{OUT} đã tồn tại, bỏ qua (dùng --force để tạo lại)")
        return

    salt, pw, it = hash_password(args.password)
    cfg = {
        "defaultACLAccess": {"publishClientSend": False, "publishClientReceive": True,
                             "subscribe": False, "unsubscribe": True},
        "clients": [{"username": "backend", "textname": "Backend server",
                     "roles": [{"rolename": "backend", "priority": -1}],
                     "salt": salt, "password": pw, "iterations": it}],
        "groups": [],
        "roles": [{"rolename": "backend", "acls": [
            acl("publishClientSend", "$CONTROL/dynamic-security/#"),
            acl("publishClientReceive", "$CONTROL/dynamic-security/#"),
            acl("subscribePattern", "$CONTROL/dynamic-security/#"),
            acl("publishClientSend", "classroom/#"),
            acl("publishClientReceive", "classroom/#"),
            acl("subscribePattern", "classroom/#"),
        ]}],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print("Đã tạo", OUT)


if __name__ == "__main__":
    main()
