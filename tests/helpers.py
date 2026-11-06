from decimal import Decimal

from signer.policy import Rule

NOW = 1729500000000
MAX_AGE_MS = 10_000
RULE_BN = Rule(max_leverage=5, allowed={"BTCUSDT": Decimal("0.01")})
RULE_HL = Rule(max_leverage=5, allowed={0: Decimal("0.01")})  # 0 = BTC trên mainnet
CLOID = "0x8c1f0000000000000000000000000001"
SUB_ACCOUNT = "0x" + "5b" * 20
USDC_TOKEN = "USDC:0x6d1e7cde53ba9467b783cb7c530ce054"

# RFC 8032, mục 7.1, TEST 1 — dùng làm key cho test
ED25519_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
SECP256K1_KEY = bytes.fromhex("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")


def bn(**overrides) -> str:
    """Payload Binance: dict giữ thứ tự → query string, None = bỏ tham số."""
    return "&".join(f"{k}={v}" for k, v in overrides.items() if v is not None)


BN = {
    "place_order": dict(symbol="BTCUSDT", side="BUY", type="LIMIT", timeInForce="GTC", quantity="0.005",
                        price="60000", newClientOrderId=CLOID, recvWindow="2000", timestamp=str(NOW)),
    "cancel_order": dict(symbol="BTCUSDT", origClientOrderId=CLOID, recvWindow="2000", timestamp=str(NOW)),
    "place_conditional": dict(algoType="CONDITIONAL", symbol="BTCUSDT", side="SELL", type="STOP_MARKET",
                              triggerPrice="55000", closePosition="true", workingType="MARK_PRICE",
                              clientAlgoId=CLOID, recvWindow="2000", timestamp=str(NOW)),
    "cancel_conditional": dict(clientAlgoId=CLOID, recvWindow="2000", timestamp=str(NOW)),
    "set_leverage": dict(symbol="BTCUSDT", leverage="3", recvWindow="2000", timestamp=str(NOW)),
}


def hl(op: str) -> dict:
    """Payload Hyperliquid hợp lệ cho từng op (bản mới mỗi lần gọi)."""
    if op == "place_order":
        action = {"type": "order",
                  "orders": [{"a": 0, "b": True, "p": "60000", "s": "0.005", "r": False,
                              "t": {"limit": {"tif": "Gtc"}}, "c": CLOID}],
                  "grouping": "na"}
    elif op == "place_conditional":
        action = {"type": "order",
                  "orders": [{"a": 0, "b": False, "p": "54000", "s": "0.005", "r": True,
                              "t": {"trigger": {"isMarket": True, "triggerPx": "55000", "tpsl": "sl"}}, "c": CLOID}],
                  "grouping": "positionTpsl"}
    elif op == "sub_account_transfer":
        action = {"type": "subAccountTransfer", "subAccountUser": SUB_ACCOUNT, "isDeposit": True, "usd": 1_000_000}
    elif op == "sub_account_spot_transfer":  # cùng op sub_account_transfer, action spot
        action = {"type": "subAccountSpotTransfer", "subAccountUser": SUB_ACCOUNT, "isDeposit": False,
                  "token": USDC_TOKEN, "amount": "12.5"}
    elif op == "earn_transfer":
        action = {"type": "borrowLend", "operation": "supply", "token": 0, "amount": "100"}
    elif op == "create_sub_account":
        action = {"type": "createSubAccount", "name": "bot-1"}
    elif op in ("cancel_order", "cancel_conditional"):
        action = {"type": "cancelByCloid", "cancels": [{"asset": 0, "cloid": CLOID}]}
    else:
        action = {"type": "updateLeverage", "asset": 0, "isCross": True, "leverage": 3}
    return {"action": action, "nonce": NOW}
