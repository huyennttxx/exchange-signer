STATUS = {
    "INVALID_REQUEST": 400,
    "UNAUTHENTICATED": 401,
    "OP_NOT_ALLOWED": 403,
    "OP_MISMATCH": 403,
    "PARAM_NOT_ALLOWED": 403,
    "SYMBOL_NOT_ALLOWED": 403,
    "TOO_MANY_ORDERS": 403,
    "QTY_EXCEEDS_LIMIT": 403,
    "LEVERAGE_EXCEEDS_LIMIT": 403,
    "MUST_CLOSE_POSITION": 403,
    "STALE_PAYLOAD": 403,
    "KEY_NOT_LOADED": 503,
    "INTERNAL": 500,
}

# Lỗi 403 từ OP_NOT_ALLOWED trở xuống: dấu hiệu BE lỗi hoặc bị chiếm quyền → cảnh báo
ALERT = {code for code, status in STATUS.items() if status == 403}


class Reject(Exception):
    def __init__(self, code: str, message: str, detail: str | None = None):
        assert code in STATUS, code
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail  # chỉ ghi log, không trả cho caller

    @property
    def status(self) -> int:
        return STATUS[self.code]

    def body(self) -> dict:
        return {"error": self.code, "message": self.message}
