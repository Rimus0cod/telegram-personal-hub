import hashlib, hmac, json, time
import pytest
from fastapi import HTTPException
from app.services.telegram_auth import validate_init_data

def signed_data(token="test-token", **overrides):
    fields={"auth_date":str(int(time.time())),"user":json.dumps({"id":123,"first_name":"Test"},separators=(",",":"))}
    fields.update(overrides)
    data="\n".join(f"{k}={v}" for k,v in sorted(fields.items()))
    secret=hmac.new(b"WebAppData",token.encode(),hashlib.sha256).digest()
    fields["hash"]=hmac.new(secret,data.encode(),hashlib.sha256).hexdigest()
    from urllib.parse import urlencode
    return urlencode(fields)

def test_valid_signed_init_data():
    assert validate_init_data(signed_data(),"test-token")["id"] == 123

def test_valid_init_data_with_telegram_signature_field():
    # When present, signature is part of Telegram's bot-token HMAC check string.
    assert validate_init_data(signed_data(signature="opaque-ed25519-value"),"test-token")["id"] == 123

def test_signature_tampering_is_rejected():
    with pytest.raises(HTTPException) as error: validate_init_data(signed_data(user='{"id":999}'),"test-token")
    assert error.value.status_code == 401

def test_expired_init_data_is_rejected():
    with pytest.raises(HTTPException) as error: validate_init_data(signed_data(auth_date=str(int(time.time())-90000)),"test-token")
    assert error.value.status_code == 401
