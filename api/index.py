from fastapi import FastAPI, Query, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from pymongo import MongoClient
import os
import re
import requests
import concurrent.futures
from datetime import datetime, timedelta

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URI = os.environ.get("MONGO_URI", "")
client = MongoClient(MONGO_URI) if MONGO_URI else None

# TỪ ĐIỂN TỌA ĐỘ CHUẨN XÁC ĐẾN TỪNG HUB
HUB_LOCATIONS = {
    "thâm quyến": {"lat": 22.5431, "lng": 114.0579},
    "nghĩa ô": {"lat": 29.3068, "lng": 120.0750},
    "bw soc": {"lat": 11.0067, "lng": 106.5139},
    "hn mê linh": {"lat": 21.1828, "lng": 105.7142},
    "mê linh soc": {"lat": 21.1828, "lng": 105.7142},
    "từ sơn": {"lat": 21.1167, "lng": 105.9500},
    "bn a mega": {"lat": 21.0828, "lng": 105.9767}, 
    "hni thanh tri": {"lat": 20.9634, "lng": 105.8156}, 
    "30-tha": {"lat": 19.3833, "lng": 105.7833}, 
    "tĩnh gia": {"lat": 19.3833, "lng": 105.7833},
    "nghi sơn": {"lat": 19.3833, "lng": 105.7833},
    "hải ninh": {"lat": 19.4167, "lng": 105.7833},
    "long bien": {"lat": 21.0475, "lng": 105.8828},
}

class TrackingRequest(BaseModel):
    trackings: List[str]

# ==========================================
# API LẤY THÔNG TIN TỪ DODANHVU VÀ LƯU MONGODB 
# (Dùng cho cả Nút Tra Cứu và Auto-Sync)
# ==========================================
def parse_jina_text(text):
    lines = text.split('\n')
    records = []
    current_record = None
    time_pattern = re.compile(r'^\s*(\d{2}:\d{2}\s\d{1,2}/\d{1,2}/\d{4})\s*$')
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        time_match = time_pattern.match(line)
        if time_match:
            if current_record: records.append(current_record)
            current_record = {"time": time_match.group(1), "desc": "", "currentLoc": "", "nextLoc": "", "emoji": "›"}
        elif current_record:
            if line.startswith('📍'):
                current_record["currentLoc"] = line.replace('📍', '').replace('[Định vị ↗]', '').strip()
            elif line.startswith('➡️'):
                current_record["nextLoc"] = line.replace('➡️', '').strip()
            elif not current_record["desc"]:
                current_record["desc"] = line
                
    if current_record: records.append(current_record)
    return records

def fetch_jina_tracking(t_code):
    url = f"https://r.jina.ai/https://tramavandon.com/spx/?tracking_number={t_code}"
    headers = {
        "Authorization": "Bearer jina_00cf8ad6eb9f4dbd9c6ad1ad23333bd5pnDu1Dx3MCjzPvZwtBKnatLo3x5d",
        "X-Return-Format": "text"
    }
    try:
        res = requests.get(url, headers=headers, timeout=20)
        if res.status_code == 200:
            return t_code, parse_jina_text(res.text)
    except Exception:
        pass
    return t_code, []

@app.post("/api/spx")
def proxy_spx_and_save(req: TrackingRequest):
    trackings = list(set(req.trackings))
    results = []
    
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_tcode = {executor.submit(fetch_jina_tracking, t_code): t_code for t_code in trackings}
            for future in concurrent.futures.as_completed(future_to_tcode):
                t_code, records = future.result()
                if records:
                    results.append({"tracking": t_code, "records": records, "status": records[0].get("desc", "Đang xử lý")})
        
        # Cập nhật ngược lên MongoDB ngay lập tức
        if client and results:
            db = client['shop_database']
            for r in results:
                t_code = r.get("tracking")
                records = r.get("records", [])
                if not records: continue
                
                latest_record = records[0]
                new_status = latest_record.get("desc") or r.get("status")
                
                formatted_history = []
                for rec in records:
                    time_str = rec.get("time", "")
                    emoji = rec.get("emoji", "›")
                    desc = rec.get("desc", "")
                    current_loc = rec.get("currentLoc", "")
                    next_loc = rec.get("nextLoc", "")
                    
                    full_desc = f"<strong style='color:#fff;'>{emoji} {desc}</strong>"
                    
                    if current_loc:
                        full_desc += f"<br><span class='spx-loc-data' style='color:#9ca3af;font-size:11.5px;display:inline-block;margin-top:3px' data-loc='{current_loc}'>📍 {current_loc}</span>"
                    if next_loc:
                        full_desc += f"<br><span style='color:#60a5fa;font-size:11.5px;display:inline-block;margin-top:3px'>→ {next_loc}</span>"
                        
                    formatted_history.append({"time": time_str, "description": full_desc})
                
                db['orders'].update_many(
                    {"items.spx_code": re.compile(f"^{t_code}$", re.IGNORECASE)},
                    {"$set": {
                        "items.$[elem].spx_stage": new_status,
                        "items.$[elem].tracking_history": formatted_history
                    }},
                    array_filters=[{"elem.spx_code": re.compile(f"^{t_code}$", re.IGNORECASE)}]
                )
        return {"results": results}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


def sync_spx_logic():
    if not client: return
    db = client['shop_database']
    query = {"status": {"$nin": ["Thành công", "Đã giao", "Đã hủy"]}}
    orders = list(db['orders'].find(query).sort("created_at", -1).limit(100))
    
    trackings_to_check = set()
    for o in orders:
        items = o.get("items", [])
        for idx, item in enumerate(items):
            spx_code = str(item.get("spx_code", "")).strip().upper()
            if spx_code.startswith("SPX") or spx_code.startswith("VN"):
                trackings_to_check.add(spx_code)

    if not trackings_to_check: return

    # Tái sử dụng lại logic gọi API ở trên để lưu
    req = TrackingRequest(trackings=list(trackings_to_check))
    proxy_spx_and_save(req)

@app.get("/")
def home():
    return {"status": "Backend Python đang chạy mượt mà trên Vercel!"}

@app.get("/api/orders")
def get_user_orders(user_id: str = Query(...), background_tasks: BackgroundTasks = BackgroundTasks()):
    try:
        background_tasks.add_task(sync_spx_logic)

        if not client: return JSONResponse(status_code=500, content={"detail": "Chưa kết nối MongoDB"})
        db = client['shop_database']
        
        try: uid = int(user_id)
        except: uid = user_id
        
        orders = list(db['orders'].find({"$or": [{"user_id": uid}, {"user_id": str(user_id)}]}).sort("created_at", -1).limit(30))
        result = []
        
        for o in orders:
            receiver_address = str(o.get("address", ""))
            raw_items = o.get("items")
            if not isinstance(raw_items, list): raw_items = []
            if len(raw_items) == 0:
                raw_items = [{"link": o.get("product_link", "Đơn hàng"), "carrier": o.get("carrier", "N/A"), "spx_code": o.get("spx_code", ""), "spx_stage": o.get("spx_stage", o.get("status", "")), "advance_payment": o.get("advance_payment", 0), "tracking_history": []}]

            items_data = []
            for item in raw_items:
                if not isinstance(item, dict): continue 
                
                current_stage = str(item.get("spx_stage") or o.get("status") or "Đang xử lý")
                t_history = item.get("tracking_history")
                safe_history = []
                if isinstance(t_history, list):
                    for h in t_history:
                        if isinstance(h, dict): safe_history.append({"time": str(h.get("time", "")), "description": str(h.get("description", ""))})
                            
                try: adv_pay = float(item.get("advance_payment", 0))
                except: adv_pay = 0

                items_data.append({
                    "link": str(item.get("link", "")), "carrier": str(item.get("carrier", "")), "spx_code": str(item.get("spx_code", "")),
                    "spx_stage": current_stage, "advance_payment": adv_pay, "tracking_history": safe_history,
                })
                
            dt = o.get("created_at")
            if isinstance(dt, datetime): dt_str = dt.strftime("%d/%m/%Y %H:%M")
            else: dt_str = str(dt) if dt else datetime.now().strftime("%d/%m/%Y %H:%M")
            
            try: price_val = float(o.get("price", 0))
            except: price_val = 0
                
            result.append({"order_id": str(o.get("order_id", "")), "status": str(o.get("status", "Đang xử lý")), "product_name": str(o.get("product_name", "Đơn hàng mới")), "price": price_val, "created_at": dt_str, "receiver_name": str(o.get("receiver_name", "")), "phone": str(o.get("phone", "")), "address": receiver_address, "items": items_data})
        return result
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.post("/api/sync_spx_from_db")
def sync_spx_from_db(user_id: str = Query(None)):
    sync_spx_logic()
    return {"message": "Đã sync thủ công xong"}

@app.get("/api/stats")
def get_web_stats(user_id: int = Query(0)):
    try:
        if not client: return {"online": 1, "monthly": 1}
        db = client['shop_database']
        now = datetime.utcnow()
        current_month = now.strftime("%Y-%m")
        if user_id != 0:
            db['web_stats'].update_one({"user_id": user_id, "month": current_month}, {"$set": {"last_active": now}}, upsert=True)
        three_mins_ago = now - timedelta(minutes=3)
        return {
            "online": max(1, db['web_stats'].count_documents({"last_active": {"$gte": three_mins_ago}})),
            "monthly": max(1, db['web_stats'].count_documents({"month": current_month}))
        }
    except:
        return {"online": 1, "monthly": 1}
