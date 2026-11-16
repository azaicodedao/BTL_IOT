"""Sao chép firmware/common/SmartProvision.h vào từng thư mục sketch (Arduino bắt buộc file nằm cạnh .ino).

    python firmware/sync_common.py          # sao chép
    python firmware/sync_common.py --check  # chỉ kiểm tra, thoát mã 1 nếu có bản sao lệch
"""
import sys
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = HERE / "common" / "SmartProvision.h"
SKETCHES = ["node_a_cam", "node_b_lcd"]


def main() -> int:
    check = "--check" in sys.argv
    data = SOURCE.read_bytes()
    stale = 0
    for sketch in SKETCHES:
        target = HERE / sketch / SOURCE.name
        if target.exists() and target.read_bytes() == data:
            print(f"[OK]  {sketch}/{SOURCE.name}")
            continue
        stale += 1
        if check:
            print(f"[LECH] {sketch}/{SOURCE.name}")
        else:
            target.write_bytes(data)
            print(f"[COPY] {sketch}/{SOURCE.name}")
    return 1 if check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
