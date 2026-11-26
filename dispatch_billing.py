"""读取平台账单，不使用 Token 单价推算实际消费。"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class BillingError(ValueError):
    pass


def amount(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BillingError("消费接口没有返回有效金额") from None
    if not result.is_finite() or result < 0:
        raise BillingError("消费接口返回的金额无效")
    return float(result)


def fingerprint(base_url, key):
    return hashlib.sha256((base_url.rstrip("/") + "\0" + key).encode()).hexdigest()


def convert_costs(costs, currency, usd_cny_rate=None):
    total = 0.0
    for unit, value in costs.items():
        if unit == currency:
            total += value
        elif {unit, currency} == {"CNY", "USD"} and usd_cny_rate:
            total += value * usd_cny_rate if unit == "USD" else value / usd_cny_rate
        elif value:
            raise BillingError("账单与预算币种不同，请填写美元兑人民币的预算换算汇率")
    return total


class BillingClient:
    def __init__(self, path):
        self.path = Path(path)

    def cached(self, platform_id, key_id, base_url, key):
        try:
            entries = json.loads(self.path.read_text(encoding="utf-8"))
            row = entries.get(platform_id + ":" + key_id, {})
            return row if row.get("fingerprint") == fingerprint(base_url, key) else {}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save(self, platform_id, key_id, base_url, key, snapshot):
        from dispatch_engine import save_json
        try:
            entries = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entries = {}
        entries[platform_id + ":" + key_id] = {**snapshot, "fingerprint": fingerprint(base_url, key)}
        save_json(self.path, entries)

    async def read(self, platform, entry, key):
        root = urlsplit(platform.base_url)
        origin = root.scheme + "://" + root.netloc
        deepseek = platform.protocol == "deepseek"
        path = "/user/balance" if deepseek else "/v1/usage"
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                response = await client.get(origin + path, headers={"Authorization": "Bearer " + key})
                response.raise_for_status()
                data = response.json()
            if deepseek:
                rows = data.get("balance_infos") or []
                row = next((row for row in rows if row.get("currency") == "CNY"), rows[0] if rows else {})
                value = amount(row.get("total_balance"))
                currency = row.get("currency")
                snapshot = {"kind": "balance", "value": value, "balance": value, "currency": currency,
                            "source": "DeepSeek 账户余额", "scope": "account"}
            else:
                total = data.get("usage", {}).get("total", {})
                value = amount(total.get("actual_cost"))
                currency = data.get("unit")
                snapshot = {"kind": "total", "value": value, "actual_total": value,
                            "balance": amount(data["balance"]) if data.get("balance") is not None else None,
                            "currency": currency, "source": "平台累计实际消费", "scope": "key"}
            if currency not in {"CNY", "USD"}:
                raise BillingError("消费接口没有返回支持的账单币种")
            snapshot.update(status="ok", checked_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            if isinstance(exc, BillingError):
                message = str(exc)
            elif isinstance(exc, httpx.HTTPStatusError):
                message = f"消费接口返回 HTTP {exc.response.status_code}，请核对平台是否支持账单查询"
            else:
                message = "暂时无法读取消费接口，请检查平台地址和网络"
            self._save(platform.id, entry.id, platform.base_url, key, {"status": "error", "error": message,
                       "checked_at": datetime.now(timezone.utc).isoformat()})
            raise BillingError(message) from None
        self._save(platform.id, entry.id, platform.base_url, key, snapshot)
        return snapshot
