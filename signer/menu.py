"""Menu quản lý key (signer.sh): hỏi người vận hành rồi gọi các lệnh của signer.admin.

Có thêm tham số thì chạy thẳng lệnh signer.admin, ví dụ: python -m signer.menu config-edit
Đổi passphrase và config không có trong menu: dùng change-passphrase, config-show, config-edit, config-set.
"""
import argparse
import sys

from signer import admin, config, keyops, keystore

MENU = [
    ("1", "Xem key (danh sách, public key / địa chỉ agent, HMAC secret, xoá)"),
    ("2", "Thêm key mới (sinh key trên máy này)"),
    ("3", "Nhập key có sẵn"),
    ("q", "Thoát"),
]
RELOAD_HINT = "Áp dụng cho Signer đang chạy: kill -HUP <pid Signer> (Linux), hoặc restart Signer."


class Menu:
    def __init__(self, config_path: str, keystore_path: str | None):
        self.config_path = config_path
        self.keystore_path = keystore_path or config.keystore_path(config_path)

    def admin(self, *argv) -> bool:
        """Chạy một lệnh signer.admin; lỗi thì in ra và trả False thay vì thoát khỏi menu."""
        try:
            admin.main(["--config", self.config_path, "--keystore", self.keystore_path, *argv])
            return True
        except SystemExit as e:
            if e.code not in (None, 0):
                print(e.code if isinstance(e.code, str) else f"failed (exit {e.code})")
            return e.code in (None, 0)

    @staticmethod
    def choose(title: str, options: list[str], cancel: str = "huỷ") -> int | None:
        """Hỏi chọn một mục theo số thứ tự; Enter = huỷ (None)."""
        print(title)
        for i, opt in enumerate(options, 1):
            print(f"  {i}) {opt}")
        while True:
            c = input(f"Chọn [1-{len(options)}], Enter để {cancel}: ").strip()
            if not c:
                return None
            if c.isdigit() and 1 <= int(c) <= len(options):
                return int(c) - 1
            print("Lựa chọn không hợp lệ.")

    def ask_venue(self) -> str | None:
        i = self.choose("Sàn:", list(keystore.VENUES))
        return None if i is None else keystore.VENUES[i]

    @staticmethod
    def show_ips(ips: str | None) -> str:
        return "(chưa có trong config)" if ips is None else ips or "(chưa có IP)"

    def pick_key(self, title: str = "Key:", cancel: str = "huỷ") -> dict | None:
        try:
            keys = keyops.list_keys(self.keystore_path)
        except keyops.NotFound:
            print(f"Chưa có keystore {self.keystore_path}: thêm key đầu tiên để tạo.")
            return None
        if not keys:
            print("Chưa có key nào.")
            return None
        ips = config.read_key_ips(self.config_path)
        for k in keys:
            k["allowed_ips"] = ips.get(k["key_id"])
        i = self.choose(title, [f"{k['key_id']}  {k['venue']:<12} {self.show_ips(k['allowed_ips'])}"
                                + ("" if k["hmac"] else "  (chưa có HMAC)") for k in keys], cancel)
        return None if i is None else keys[i]

    def add_key(self, command: str) -> None:
        # Không hỏi IP: IP được phép sửa tay trong config.ini
        venue = self.ask_venue()
        if venue and self.admin(command, "--venue", venue):
            print(f"Key chưa có IP được phép: điền allowed_ips trong section của key ở {self.config_path}.")
            print(RELOAD_HINT)

    def view(self) -> None:
        # Danh sách không cần passphrase; chỉ khi chọn key rồi chọn việc mới hỏi
        key = self.pick_key("Key (cột: key_id, sàn, IP được phép). Chọn một key để xem public key, "
                            "tạo HMAC secret hoặc xoá:", "quay lại")
        if not key:
            return
        i = self.choose(f"Key {key['key_id']} ({key['venue']}):",
                        ["Xem public key / địa chỉ agent", "Tạo HMAC secret mới", "Xoá key"], "quay lại")
        if i == 0:
            self.admin("show", key["key_id"])
        elif i == 1:
            self.rotate_hmac(key)
        elif i == 2:
            self.remove(key)

    def rotate_hmac(self, key: dict) -> None:
        if key["hmac"] and input(f"Key {key['key_id']} đã có HMAC secret. Secret cũ sẽ hết hiệu lực, "
                                 "BE phải cập nhật secret mới. Gõ 'yes' để tiếp tục: ").strip() != "yes":
            print("Đã huỷ.")
            return
        if self.admin("rotate-hmac", key["key_id"]):
            print(RELOAD_HINT)

    def remove(self, key: dict) -> None:
        if input(f"Xoá key {key['key_id']} ({key['venue']})? Không khôi phục được. Gõ 'yes' để xoá: ").strip() != "yes":
            print("Đã huỷ.")
            return
        if self.admin("remove", key["key_id"]):
            print(RELOAD_HINT)
            print("Nhớ thu hồi key này trên sàn ngay.")

    def handle(self, choice: str) -> bool:
        """Chạy một mục; trả False khi người dùng chọn thoát."""
        actions = {
            "1": self.view,
            "2": lambda: self.add_key("generate"),
            "3": lambda: self.add_key("import"),
        }
        if choice == "q":
            return False
        if choice not in actions:
            print("Lựa chọn không hợp lệ.")
            return True
        try:
            actions[choice]()
        except KeyboardInterrupt:
            print("\nĐã huỷ.")
        input("\nNhấn Enter để quay lại menu...")
        return True

    def loop(self) -> None:
        while True:
            print(f"\n===== Signer: {self.config_path} · keystore: {self.keystore_path} =====")
            for key, label in MENU:
                print(f"  {key}) {label}")
            try:
                choice = input("Chọn: ").strip().lower()
                print()
                if not self.handle(choice):
                    return
            except (EOFError, KeyboardInterrupt):
                print()
                return


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=config.DEFAULT_CONFIG, help="default: %(default)s")
    ap.add_argument("--keystore", help=f"default: the keystore in --config, else {config.DEFAULT_KEYSTORE}")
    a, rest = ap.parse_known_args(argv)
    if rest:
        admin.main(argv)
        return
    # Menu có tiếng Việt: terminal không phải UTF-8 (pipe trên Windows, LANG=C) thì print bị UnicodeEncodeError
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        menu = Menu(a.config, a.keystore)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot read {a.config}: {e}")
    menu.loop()


if __name__ == "__main__":
    main()
