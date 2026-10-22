"""Quản lý keystore và config. Chạy trực tiếp trên máy Signer.

IP được phép, symbol, hạn mức: sửa tay section [key:…] trong config.
Đổi key hay section [key:…]: áp dụng bằng kill -HUP <pid Signer> (Linux) hoặc restart.
Đổi [server]: phải restart.
"""
import argparse
import getpass
import os
import sys

from signer import config, keyops, keystore


def _passphrase() -> bytes:
    return getpass.getpass("Keystore passphrase: ").encode()


def _new_passphrase() -> bytes:
    p1 = getpass.getpass("New passphrase: ")
    if p1 != getpass.getpass("Repeat passphrase: "):
        sys.exit("passphrases do not match")
    return p1.encode()


def _print_public(public: dict) -> None:
    if public["kind"] == "ed25519_public_key_pem":
        print("Ed25519 public key (paste into Binance when creating the API key):\n" + public["value"])
    else:
        print("Agent address (approve it from the main wallet on Hyperliquid): " + public["value"])


def _print_hmac(key_id: str, hmac_secret: str) -> None:
    # Signer không in lại được secret này: mất thì chạy rotate-hmac
    print(f"HMAC secret for {key_id} (give it to BE, shown only once; lost → rotate-hmac):\n{hmac_secret}")


def _add(a, secret: bytes | None) -> None:
    if keyops.exists(a.keystore):
        passphrase = _passphrase()
    else:
        # Lần đầu: tạo keystore luôn. Hỏi passphrase mới 2 lần, để gõ nhầm đường dẫn thì dễ nhận ra
        print(f"{a.keystore} not found, creating a new keystore")
        passphrase = _new_passphrase()
    result = keyops.add(a.keystore, passphrase, a.venue, secret)
    print(f"key_id: {result['key_id']}")
    _print_public(result["public"])
    _print_hmac(result["key_id"], result["hmac_secret"])
    try:
        section = config.add_key_section(a.config, a.keystore, result["key_id"])
        print(f"added to {a.config}:\n{section}", end="")
        print(f"No allowed IP yet: fill in allowed_ips (and symbols, max_leverage) in {a.config} by hand")
    except (OSError, ValueError) as e:
        # Key đã lưu trong keystore: chỉ thiếu section, thêm tay được
        section = config.new_key_section(result["key_id"])
        print(f"WARNING: cannot update {a.config} ({e}). Add this section by hand:\n{section}", end="")


def cmd_init(a) -> None:
    if keyops.exists(a.keystore):
        sys.exit(f"{a.keystore} already exists")
    keyops.create(a.keystore, _new_passphrase())
    print(f"created {a.keystore}")


def cmd_generate(a) -> None:
    _add(a, None)


def cmd_import(a) -> None:
    raw = getpass.getpass("Private key (hex, 32 bytes): ").strip().removeprefix("0x")
    try:
        secret = bytes.fromhex(raw)
    except ValueError:
        sys.exit("private key must be hex")
    _add(a, secret)


def cmd_rotate_hmac(a) -> None:
    _print_hmac(a.key_id, keyops.rotate_hmac(a.keystore, _passphrase(), a.key_id))
    print("The old secret stops working once the signer reloads the keystore")


def cmd_remove(a) -> None:
    keyops.remove(a.keystore, _passphrase(), a.key_id)
    print(f"removed {a.key_id}")
    if config.remove_key_section(a.config, a.key_id):
        print(f"removed [key:{a.key_id}] from {a.config}")


def cmd_list(a) -> None:
    ips = config.read_key_ips(a.config)
    for k in keyops.list_keys(a.keystore):
        shown = ips.get(k["key_id"])
        print(f"{k['key_id']}  {k['venue']:<12} {'(no config section)' if shown is None else shown or '(no IP)'}"
              + ("" if k["hmac"] else "  (no HMAC)"))


def cmd_show(a) -> None:
    _print_public(keyops.show(a.keystore, _passphrase(), a.key_id))


def cmd_change_passphrase(a) -> None:
    keyops.change_passphrase(a.keystore, _passphrase(), _new_passphrase())
    print("passphrase changed. Restart the signer: it cannot reload a keystore with a new passphrase")


def _show_value(v) -> str:
    return "(empty)" if v is None or v == "" else str(v)


def cmd_config_show(a) -> None:
    exists = os.path.exists(a.config)
    raw = config.load_raw(a.config) if exists else {config.SERVER: {}, "keys": {}}
    print(f"config:   {os.path.abspath(a.config)}" + ("" if exists else " (not found)"))
    print(f"keystore: {config.get(raw, 'keystore') if exists else a.keystore}")
    for f in config.FIELDS:
        print(f"  {f:<20} {_show_value(config.get(raw, f))}")
    for key_id, opts in raw["keys"].items():
        print(f"[key:{key_id}]")
        for k, v in opts.items():
            print(f"  {k:<20} {v}")
    if keyops.exists(a.keystore):
        for k in keyops.list_keys(a.keystore):
            if k["key_id"] not in raw["keys"]:
                print(f"key {k['key_id']} ({k['venue']}): no [key:…] section, cannot sign")
    if exists:
        try:
            config.parse(raw)
        except ValueError as e:
            print(f"INVALID, the signer will not start: {e}")


def _save_config(a, changes: dict) -> None:
    config.update(a.config, a.keystore, changes)
    print(f"saved {a.config}. Restart the signer to apply")


def cmd_config_set(a) -> None:
    changes = {}
    for item in a.assignment:
        field, sep, value = item.partition("=")
        if not sep:
            sys.exit(f"expected FIELD=VALUE, got {item!r}")
        changes[field.strip()] = value.strip()
    _save_config(a, changes)


def cmd_config_edit(a) -> None:
    new = not os.path.exists(a.config)
    # Field chưa có thì hiện giá trị mặc định
    raw = {config.SERVER: {}, "keys": {}} if new else config.load_raw(a.config)
    print(f"{'Creating' if new else 'Editing'} {os.path.abspath(a.config)}. Enter keeps the value in [brackets].")
    values, diff = {}, []
    for f in config.FIELDS:
        old = config.get(raw, f)
        old_text = "" if old is None else str(old)
        value = input(f"  {f} [{old_text}]: ").strip()
        if value:
            values[f] = value
        if value and value != old_text or new:
            diff.append(f"  {f}: {_show_value(old)} -> {_show_value(value or old)}")
    if not diff:
        print("nothing changed")
        return
    print("Changes:\n" + "\n".join(diff))
    if input("Save? [y/N] ").strip().lower() != "y":
        print("cancelled")
        return
    _save_config(a, values)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=config.DEFAULT_CONFIG, help="used to find the keystore (default: %(default)s)")
    ap.add_argument("--keystore", help=f"default: the keystore in --config, else {config.DEFAULT_KEYSTORE}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create an empty keystore").set_defaults(fn=cmd_init)
    for name, fn in (("generate", cmd_generate), ("import", cmd_import)):
        p = sub.add_parser(name)
        p.add_argument("--venue", required=True, choices=keystore.VENUES)
        p.set_defaults(fn=fn)
    for name, fn in (("remove", cmd_remove), ("show", cmd_show), ("rotate-hmac", cmd_rotate_hmac)):
        p = sub.add_parser(name)
        p.add_argument("key_id")
        p.set_defaults(fn=fn)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("change-passphrase").set_defaults(fn=cmd_change_passphrase)
    sub.add_parser("config-show", help="print the config").set_defaults(fn=cmd_config_show)
    p = sub.add_parser("config-set", help=f"set config fields: {', '.join(config.FIELDS)}")
    p.add_argument("assignment", nargs="+", metavar="FIELD=VALUE")
    p.set_defaults(fn=cmd_config_set)
    sub.add_parser("config-edit", help="edit the config field by field").set_defaults(fn=cmd_config_edit)

    a = ap.parse_args(argv)
    try:
        a.keystore = a.keystore or config.keystore_path(a.config)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot read {a.config}: {e}")
    try:
        a.fn(a)
    except keyops.KeyOpError as e:
        sys.exit(str(e))
    except ValueError as e:  # config không hợp lệ: không ghi gì
        sys.exit(f"config error: {e}")


if __name__ == "__main__":
    main()
