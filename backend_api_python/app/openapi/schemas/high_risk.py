"""Typed request and response contracts for security-sensitive human APIs."""

from __future__ import annotations

from marshmallow import Schema, ValidationError, fields, pre_load, validate, validates_schema


class LoginRequestSchema(Schema):
    username = fields.String(validate=validate.Length(min=1, max=254))
    account = fields.String(validate=validate.Length(min=1, max=254))
    password = fields.String(required=True, validate=validate.Length(min=1, max=1024))
    turnstile_token = fields.String(allow_none=True, validate=validate.Length(max=4096))
    turnstile_clearance = fields.String(allow_none=True, validate=validate.Length(max=4096))
    client_type = fields.String(load_default="pc", validate=validate.OneOf(["pc", "mobile"]))

    @validates_schema
    def validate_identity(self, data, **kwargs):
        if not data.get("username") and not data.get("account"):
            raise ValidationError("username or account is required", field_name="username")


class RegisterRequestSchema(Schema):
    email = fields.Email(required=True, validate=validate.Length(max=254))
    code = fields.String(required=True, validate=validate.Length(min=1, max=32))
    username = fields.String(required=True, validate=validate.Length(min=3, max=30))
    password = fields.String(required=True, validate=validate.Length(min=1, max=1024))
    referral_code = fields.String(load_default="", validate=validate.Length(max=64))
    client_type = fields.String(load_default="pc", validate=validate.OneOf(["pc", "mobile"]))


class ResetPasswordRequestSchema(Schema):
    email = fields.Email(required=True, validate=validate.Length(max=254))
    code = fields.String(required=True, validate=validate.Length(min=1, max=32))
    new_password = fields.String(required=True, validate=validate.Length(min=1, max=1024))


class ChangePasswordRequestSchema(Schema):
    code = fields.String(required=True, validate=validate.Length(min=1, max=32))
    new_password = fields.String(required=True, validate=validate.Length(min=1, max=1024))


class StrategyIdQuerySchema(Schema):
    id = fields.Integer(required=True, validate=validate.Range(min=1))


class CredentialCreateRequestSchema(Schema):
    name = fields.String(load_default="", validate=validate.Length(max=128))
    exchange_id = fields.String(required=True, validate=validate.Length(min=1, max=32))
    api_key = fields.String(load_default="", validate=validate.Length(max=4096))
    apiKey = fields.String(load_default="", validate=validate.Length(max=4096))
    secret_key = fields.String(load_default="", validate=validate.Length(max=4096))
    secretKey = fields.String(load_default="", validate=validate.Length(max=4096))
    passphrase = fields.String(load_default="", validate=validate.Length(max=4096))
    base_url = fields.String(load_default="", allow_none=True, validate=validate.Length(max=2048))
    baseUrl = fields.String(load_default="", allow_none=True, validate=validate.Length(max=2048))
    enable_demo_trading = fields.Boolean(load_default=False)
    demo = fields.Boolean(load_default=False)
    sandbox = fields.Boolean(load_default=False)
    testnet = fields.Boolean(load_default=False)
    environment = fields.String(load_default="", validate=validate.Length(max=32))
    network = fields.String(load_default="", validate=validate.Length(max=32))
    market_scope = fields.String(load_default="", validate=validate.OneOf(["", "spot", "swap", "both"]))
    marketScope = fields.String(load_default="", validate=validate.Length(max=32))
    bitget_account_mode = fields.String(
        load_default="",
        validate=validate.OneOf(["", "auto", "classic", "uta"]),
    )
    bitgetAccountMode = fields.String(load_default="", validate=validate.Length(max=32))
    ibkr_host = fields.String(load_default="127.0.0.1", validate=validate.Length(max=255))
    ibkr_port = fields.Integer(load_default=7497, validate=validate.Range(min=1, max=65535))
    ibkr_client_id = fields.Integer(load_default=7, validate=validate.Range(min=0, max=2147483647))
    ibkr_account = fields.String(load_default="", validate=validate.Length(max=128))

    @pre_load
    def normalize_exchange(self, data, **kwargs):
        normalized = dict(data or {})
        normalized["exchange_id"] = str(normalized.get("exchange_id") or "").strip().lower()
        return normalized

    @validates_schema
    def validate_exchange_secret(self, data, **kwargs):
        if str(data.get("exchange_id") or "").lower() == "ibkr":
            return
        if not (data.get("api_key") or data.get("apiKey")):
            raise ValidationError("api_key is required", field_name="api_key")
        if not (data.get("secret_key") or data.get("secretKey")):
            raise ValidationError("secret_key is required", field_name="secret_key")


class BillingOrderRequestSchema(Schema):
    plan = fields.String(
        required=True,
        validate=validate.Length(min=1, max=64),
    )
    currency = fields.String(
        load_default="USDT",
        validate=validate.OneOf(("USDT", "USDC")),
    )
    chain = fields.String(
        allow_none=True,
        load_default=None,
        validate=validate.OneOf(("TRC20", "BEP20", "ERC20", "SOL")),
    )

    @pre_load
    def normalize_values(self, data, **kwargs):
        normalized = dict(data or {})
        normalized["plan"] = str(normalized.get("plan") or "").strip().lower()
        normalized["currency"] = str(normalized.get("currency") or "USDT").strip().upper()
        if normalized.get("chain") not in (None, ""):
            normalized["chain"] = str(normalized["chain"]).strip().upper()
        else:
            normalized["chain"] = None
        return normalized


class QuickTradeOrderRequestSchema(Schema):
    asset_id = fields.Integer(load_default=0, validate=validate.Range(min=0))
    credential_id = fields.Integer(required=True, validate=validate.Range(min=1))
    symbol = fields.String(required=True, validate=validate.Length(min=1, max=64))
    side = fields.String(required=True, validate=validate.OneOf(("buy", "sell")))
    order_type = fields.String(
        load_default="market",
        validate=validate.OneOf(("market", "limit")),
    )
    amount = fields.Float(load_default=0, validate=validate.Range(min=0))
    quantity = fields.Float(load_default=0, validate=validate.Range(min=0))
    price = fields.Float(load_default=0, validate=validate.Range(min=0))
    reference_price = fields.Float(load_default=0, validate=validate.Range(min=0))
    reference_price_at_ms = fields.Integer(load_default=0, validate=validate.Range(min=0))
    leverage = fields.Integer(load_default=1, validate=validate.Range(min=1, max=125))
    market_type = fields.String(
        load_default="",
        validate=validate.OneOf(("", "spot", "swap", "futures", "future", "perp", "perpetual")),
    )
    tp_price = fields.Float(load_default=0, validate=validate.Range(min=0))
    sl_price = fields.Float(load_default=0, validate=validate.Range(min=0))
    source = fields.String(load_default="manual", validate=validate.Length(max=64))
    margin_mode = fields.String(load_default="", validate=validate.Length(max=16))
    marginMode = fields.String(load_default="", validate=validate.Length(max=16))
    instrument_id = fields.String(load_default="", validate=validate.Length(max=128))
    product_type = fields.String(load_default="", validate=validate.Length(max=64))
    api_family = fields.String(load_default="", validate=validate.Length(max=64))
    resolve_product = fields.Boolean(load_default=False)

    @pre_load
    def normalize_values(self, data, **kwargs):
        normalized = dict(data or {})
        for key in ("side", "order_type", "market_type", "margin_mode", "marginMode", "product_type", "api_family"):
            if key in normalized:
                normalized[key] = str(normalized[key] or "").strip().lower()
        return normalized

    @validates_schema
    def validate_limit_price(self, data, **kwargs):
        if data.get("order_type") == "limit" and float(data.get("price") or 0) <= 0:
            raise ValidationError("price must be greater than zero for limit orders", field_name="price")
        quantity = float(data.get("quantity") or 0)
        amount = float(data.get("amount") or 0)
        is_spot = data.get("market_type") == "spot"
        if amount > 0 and quantity > 0:
            raise ValidationError("amount and quantity are mutually exclusive", field_name="quantity")
        if amount <= 0 and not (is_spot and quantity > 0):
            raise ValidationError("amount must be greater than zero", field_name="amount")
        if quantity > 0 and not is_spot:
            raise ValidationError("quantity is only supported for spot orders", field_name="quantity")


class QuickTradeCloseRequestSchema(Schema):
    asset_id = fields.Integer(load_default=0, validate=validate.Range(min=0))
    order_type = fields.String(load_default="market", validate=validate.OneOf(("market", "limit")))
    price = fields.Float(load_default=0, validate=validate.Range(min=0))
    credential_id = fields.Integer(required=True, validate=validate.Range(min=1))
    symbol = fields.String(required=True, validate=validate.Length(min=1, max=64))
    market_type = fields.String(
        load_default="swap",
        validate=validate.OneOf(("spot", "swap", "futures", "future", "perp", "perpetual")),
    )
    size = fields.Float(load_default=0, validate=validate.Range(min=0))
    close_scope = fields.String(load_default="full", validate=validate.Length(max=32))
    closeScope = fields.String(load_default="", validate=validate.Length(max=32))
    position_side = fields.String(
        load_default="",
        validate=validate.OneOf(("", "long", "short")),
    )
    close_side = fields.String(
        load_default="",
        validate=validate.OneOf(("", "long", "short")),
    )
    source = fields.String(load_default="manual", validate=validate.Length(max=64))
    instrument_id = fields.String(load_default="", validate=validate.Length(max=128))
    product_type = fields.String(load_default="", validate=validate.Length(max=64))
    api_family = fields.String(load_default="", validate=validate.Length(max=64))
    resolve_product = fields.Boolean(load_default=False)

    @pre_load
    def normalize_values(self, data, **kwargs):
        normalized = dict(data or {})
        for key in ("market_type", "close_scope", "closeScope", "position_side", "close_side"):
            if key in normalized:
                normalized[key] = str(normalized[key] or "").strip().lower()
        return normalized


class QuickTradeCancelRequestSchema(Schema):
    trade_id = fields.Integer(required=True, validate=validate.Range(min=1))


class QuickTradeProtectionRequestSchema(Schema):
    credential_id = fields.Integer(required=True, validate=validate.Range(min=1))
    symbol = fields.String(required=True, validate=validate.Length(min=1, max=64))
    position_side = fields.String(required=True, validate=validate.OneOf(("long", "short")))
    action = fields.String(load_default="create", validate=validate.OneOf(("create", "cancel")))
    kind = fields.String(load_default="tp", validate=validate.OneOf(("tp", "sl")))
    price = fields.Float(load_default=0, validate=validate.Range(min=0))
    order_id = fields.String(load_default="", validate=validate.Length(max=64))


class UserInfoSchema(Schema):
    id = fields.Integer()
    username = fields.String()
    nickname = fields.String(allow_none=True)
    email = fields.Email(allow_none=True)
    avatar = fields.String(allow_none=True)
    timezone = fields.String(allow_none=True)
    role = fields.Raw(allow_none=True)


class LoginDataSchema(Schema):
    token = fields.String(required=True)
    userinfo = fields.Nested(UserInfoSchema, required=True)


class LoginResponseSchema(Schema):
    code = fields.Integer(required=True)
    msg = fields.String(required=True)
    data = fields.Nested(LoginDataSchema, allow_none=True)


class CredentialCreatedDataSchema(Schema):
    id = fields.Integer(required=True)


class CredentialCreatedResponseSchema(Schema):
    code = fields.Integer(required=True)
    msg = fields.String(required=True)
    data = fields.Nested(CredentialCreatedDataSchema, allow_none=True)
