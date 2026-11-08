import os
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field
from typing import Optional, Literal
import data_fetcher
import session_db

app = FastAPI(title="Chartiq Free - Market Replay Platform", version="1.0.0")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

MAX_UPLOAD_SIZE = 20 * 1024 * 1024

DEFAULT_SYMBOLS = [
    {"name": "XAU/USD (Gold)", "symbol": "XAUUSD", "category": "Commodities", "default_tf": "15m"},
    {"name": "EUR/USD", "symbol": "EURUSD", "category": "Forex", "default_tf": "15m"},
    {"name": "GBP/USD", "symbol": "GBPUSD", "category": "Forex", "default_tf": "15m"},
    {"name": "USD/JPY", "symbol": "USDJPY", "category": "Forex", "default_tf": "15m"},
    {"name": "AUD/USD", "symbol": "AUDUSD", "category": "Forex", "default_tf": "15m"},
    {"name": "USD/CAD", "symbol": "USDCAD", "category": "Forex", "default_tf": "15m"},
    {"name": "USD/CHF", "symbol": "USDCHF", "category": "Forex", "default_tf": "15m"},
    {"name": "GBP/JPY", "symbol": "GBPJPY", "category": "Forex", "default_tf": "15m"},
    {"name": "NASDAQ 100", "symbol": "NASDAQ", "category": "Indices", "default_tf": "15m"},
    {"name": "US30 (Dow Jones)", "symbol": "US30", "category": "Indices", "default_tf": "15m"},
    {"name": "S&P 500", "symbol": "US500", "category": "Indices", "default_tf": "15m"},
    {"name": "BTC/USD (Bitcoin)", "symbol": "BTCUSD", "category": "Crypto", "default_tf": "1h"},
    {"name": "ETH/USD (Ethereum)", "symbol": "ETHUSD", "category": "Crypto", "default_tf": "1h"},
]


class CreateSessionRequest(BaseModel):
    name: str
    symbol: str
    timeframe: str
    initial_balance: float = 10000.0
    notes: Optional[str] = ""


class UpdateSessionRequest(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None


class SaveTradeRequest(BaseModel):
    id: Optional[str] = None
    session_id: str
    symbol: str
    type: Literal["LONG", "SHORT"]
    entry_time: int
    exit_time: Optional[int] = None
    entry_price: float = Field(..., gt=0)
    exit_price: Optional[float] = None
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    lot_size: float = Field(..., gt=0)
    pnl: float = 0.0
    pnl_percent: float = 0.0
    rr: float = 0.0
    outcome: Literal["TP", "SL", "MANUAL", "OPEN"] = "OPEN"
    notes: Optional[str] = ""
    new_balance: Optional[float] = 10000.0


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/favicon.ico")
async def favicon():
    return HTMLResponse(content="", status_code=204)


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return "<h1>Chartiq Free is running. index.html not found yet.</h1>"


@app.get("/api/symbols")
async def get_symbols():
    return {"symbols": DEFAULT_SYMBOLS}


@app.get("/api/candles")
def get_candles(
    symbol: str = "XAUUSD",
    interval: str = "15m",
    period: str = "30d",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    result = data_fetcher.fetch_market_data(
        symbol=symbol,
        interval=interval,
        period=period,
        start_date=start_date,
        end_date=end_date,
    )
    if "error" in result:
        code = result.get("code", "BAD_INPUT")
        if code == "NO_DATA":
            raise HTTPException(status_code=404, detail=result["error"])
        if code == "UPSTREAM":
            raise HTTPException(status_code=502, detail=result["error"])
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/upload-csv")
async def upload_csv(file: UploadFile = File(...)):
    try:
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail="Ukuran file CSV terlalu besar. Maksimal 20 MB.")
        text_content = content.decode("utf-8", errors="ignore")
        result = data_fetcher.parse_csv_data(text_content)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Upload failed: {str(e)}")


@app.get("/api/sessions")
async def get_sessions():
    return {"sessions": session_db.list_sessions()}


@app.post("/api/sessions")
async def create_session_endpoint(req: CreateSessionRequest):
    sess = session_db.create_session(
        name=req.name,
        symbol=req.symbol,
        timeframe=req.timeframe,
        initial_balance=req.initial_balance,
        notes=req.notes or "",
    )
    return sess


@app.get("/api/sessions/{session_id}")
async def get_session_details(session_id: str):
    sess = session_db.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session tidak ditemukan")
    return sess


@app.patch("/api/sessions/{session_id}")
async def patch_session_endpoint(session_id: str, req: UpdateSessionRequest):
    if req.name is None and req.notes is None:
        raise HTTPException(status_code=400, detail="Minimal satu field harus diubah")
    updated = session_db.update_session(session_id, name=req.name, notes=req.notes)
    if updated is None:
        raise HTTPException(status_code=404, detail="Session tidak ditemukan")
    return updated


@app.get("/api/sessions/{session_id}/stats")
async def get_session_stats(session_id: str):
    try:
        return session_db.get_session_stats(session_id)
    except session_db.SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    session_db.delete_session(session_id)
    return {"success": True}


@app.post("/api/trades")
async def save_trade_endpoint(req: SaveTradeRequest):
    try:
        saved = session_db.save_trade(req.model_dump())
    except session_db.SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "trade": saved}


@app.delete("/api/trades/{trade_id}")
async def delete_trade_endpoint(trade_id: str):
    session_db.delete_trade(trade_id)
    return {"success": True}

