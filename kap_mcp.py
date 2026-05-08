import json
import time
import os
import httpx
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

BASE_URL = "https://www.kap.org.tr/api/vyk"
TOKEN_URL = "https://www.kap.org.tr/api/vyk/generateToken"
API_KEY   = os.environ.get("KAP_API_KEY", "29223dec-32bc-49fb-919f-51405d110ab2")
PORT      = int(os.environ.get("PORT", 8000))

_token_cache: dict = {"token": None, "expires_at": 0}

mcp = FastMCP("kap_mcp", host="0.0.0.0", port=PORT)


async def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(TOKEN_URL, json={"apiKey": API_KEY})
        resp.raise_for_status()
        data = resp.json()
        token = data.get("token") or data.get("data") or data
        if isinstance(token, dict):
            token = token.get("token", "")
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + 23 * 3600
    return token


async def _get(endpoint: str, params: dict = None) -> dict:
    token = await _get_token()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{BASE_URL}/{endpoint}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
        )
        resp.raise_for_status()
        return resp.json()


def _handle_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        s = e.response.status_code
        if s == 401:
            return "Token geçersiz veya süresi dolmuş."
        if s == 403:
            return "Erişim reddedildi."
        if s == 404:
            return "Kaynak bulunamadı."
        return f"API hatası: HTTP {s}"
    if isinstance(e, httpx.TimeoutException):
        return "İstek zaman aşımı."
    return f"Hata: {type(e).__name__}: {e}"


def _fmt_bildirim(b: dict) -> str:
    return (
        f"ID: {b.get('disclosureIndex') or b.get('id','?')} | "
        f"Şirket: {b.get('companyName') or b.get('memberDesc','?')} | "
        f"Tür: {b.get('disclosureType','?')} | "
        f"Tarih: {b.get('publishDate') or b.get('createDate','?')} | "
        f"Başlık: {b.get('header','?')}"
    )


class BildirimListeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    baslangic_tarihi: Optional[str] = Field(default=None, description="Başlangıç tarihi YYYY-MM-DD")
    bitis_tarihi: Optional[str] = Field(default=None, description="Bitiş tarihi YYYY-MM-DD")
    sirket_kodu: Optional[str] = Field(default=None, description="BIST şirket kodu. Örn: THYAO")
    limit: int = Field(default=20, ge=1, le=100)


class BildirimDetayInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    bildirim_id: int = Field(..., description="Bildirim ID numarası", gt=0)


class SirketAraInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    arama_terimi: Optional[str] = Field(default=None, description="Şirket adı veya kodu")


@mcp.tool(name="kap_bildirimleri_listele", annotations={"readOnlyHint": True, "destructiveHint": False})
async def kap_bildirimleri_listele(params: BildirimListeInput) -> str:
    """KAP bildirimlerini tarih ve şirket koduna göre filtreler ve listeler."""
    try:
        query = {}
        if params.baslangic_tarihi:
            query["startDate"] = params.baslangic_tarihi
        if params.bitis_tarihi:
            query["endDate"] = params.bitis_tarihi
        if params.sirket_kodu:
            query["memberCode"] = params.sirket_kodu.upper()
        data = await _get("disclosures", query)
        items = data if isinstance(data, list) else (data.get("content") or data.get("data") or [])
        items = items[:params.limit]
        if not items:
            return "Kriterlere uyan bildirim bulunamadı."
        return f"{len(items)} bildirim:\n" + "\n".join(_fmt_bildirim(b) for b in items)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="kap_bildirim_detay", annotations={"readOnlyHint": True, "destructiveHint": False})
async def kap_bildirim_detay(params: BildirimDetayInput) -> str:
    """Belirli bir KAP bildiriminin tam içeriğini getirir."""
    try:
        data = await _get(f"disclosureDetail/{params.bildirim_id}", {"fileType": "data"})
        baslik  = data.get("header") or data.get("title") or "?"
        sirket  = data.get("companyName") or data.get("memberDesc") or "?"
        tarih   = data.get("publishDate") or data.get("createDate") or "?"
        tur     = data.get("disclosureType") or "?"
        icerik  = data.get("content") or data.get("text") or ""
        if len(icerik) > 3000:
            icerik = icerik[:3000] + "... [kısaltıldı]"
        return f"Şirket: {sirket}\nTür: {tur}\nTarih: {tarih}\nBaşlık: {baslik}\n\n{icerik}"
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="kap_bugun_bildirimleri", annotations={"readOnlyHint": True, "destructiveHint": False})
async def kap_bugun_bildirimleri() -> str:
    """Bugün KAP'ta yayınlanan tüm bildirimleri getirir."""
    try:
        from datetime import date
        bugun = date.today().strftime("%Y-%m-%d")
        data = await _get("disclosures", {"startDate": bugun, "endDate": bugun})
        items = data if isinstance(data, list) else (data.get("content") or data.get("data") or [])
        if not items:
            return f"{bugun} tarihinde henüz bildirim yok."
        return f"Bugün {len(items)} bildirim:\n" + "\n".join(_fmt_bildirim(b) for b in items)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="kap_sirket_listesi", annotations={"readOnlyHint": True, "destructiveHint": False})
async def kap_sirket_listesi(params: SirketAraInput) -> str:
    """KAP'ta kayıtlı şirketleri listeler veya isime göre arar."""
    try:
        data = await _get("companies")
        items = data if isinstance(data, list) else (data.get("content") or data.get("data") or [])
        if params.arama_terimi:
            terim = params.arama_terimi.lower()
            items = [s for s in items if terim in str(s.get("companyName","")).lower() or terim in str(s.get("memberCode","")).lower()]
        if not items:
            return "Eşleşen şirket bulunamadı."
        satirlar = [f"[{s.get('memberCode','?')}] {s.get('companyName','?')}" for s in items[:50]]
        return f"{len(items)} şirket:\n" + "\n".join(satirlar)
    except Exception as e:
        return _handle_error(e)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
