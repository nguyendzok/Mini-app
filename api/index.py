from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
import os
import re
import requests
from datetime import datetime, timedelta

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*", "https://hoangngocnguyen.id.vn"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URI = os.environ.get("MONGO_URI")
client = MongoClient(MONGO_URI) if MONGO_URI else None

# TỪ ĐIỂN TỌA ĐỘ KHO (Dùng dự phòng)
HUB_LOCATIONS = {
    "thâm quyến": {"lat": 22.5431, "lng": 114.0579},
    "nghĩa ô": {"lat": 29.3068, "lng": 120.0750},
    "bằng tường": {"lat": 22.1150, "lng": 106.7538},
    "đông quản": {"lat": 22.0403, "lng": 113.7521},
    "bw soc": {"lat": 11.0067, "lng": 106.5139},
    "củ chi soc": {"lat": 11.0067, "lng": 106.5139},
    "hn từ liêm soc": {"lat": 21.0470, "lng": 105.7480},
    "hn mê linh soc": {"lat": 21.1828, "lng": 105.7142},
    "từ sơn soc": {"lat": 21.1167, "lng": 105.9500},
    "đà nẵng soc": {"lat": 16.0471, "lng": 108.2062},
    "hồ chí minh": {"lat": 10.8231, "lng": 106.6297},
    "hà nội": {"lat": 21.0285, "lng": 105.8542},
    "thanh hóa": {"lat": 19.8056, "lng": 105.7766},
    "trang hạ": {"lat": 21.1218, "lng": 105.9405},
    "từ sơn": {"lat": 21.1167, "lng": 105.9500},
    "tĩnh gia 2": {"lat": 19.3833, "lng": 105.7833},
}

def extract_gmap_coords(url):
    """Truy cập link Google Maps và bóc tách tọa độ (lat, lng)"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(url, headers=headers, timeout=5, allow_redirects=True)
        final_url = res.url
        
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', final_url)
        if match: return {"lat": float(match.group(1)), "lng": float(match.group(2))}
            
        match_q = re.search(r'[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)', final_url)
        if match_q: return {"lat": float(match_q.group(1)), "lng": float(match_q.group(2))}
            
        match_s = re.search(r'search/(-?\d+\.\d+),(-?\d+\.\d+)', final_url)
        if match_s: return {"lat": float(match_s.group(1)), "lng": float(match_s.group(2))}
    except Exception as e:
        print("Lỗi bóc tách link GG Maps:", e)
    return None

def guess_coordinates(text, fallback_lat=19.3833, fallback_lng=105.7833):
    if not text:
        return {"lat": fallback_lat, "lng": fallback_lng}
    
    text_lower = str(text).lower().strip()
    
    # NẾU CÓ LINK GOOGLE MAPS -> Ưu tiên bóc tách ngay
    if "http" in text_lower and ("maps" in text_lower or "goo.gl" in text_lower or "googleusercontent" in text_lower):
        url_match = re.search(r'(https?://[^\s]+)', text)
        if url_match:
            coords = extract_gmap_coords(url_match.group(1))
            if coords: return coords

    # Dùng từ điển nội suy
    for key, coords in HUB_LOCATIONS.items():
        if key in text_lower:
            return coords
            
    return {"lat": fallback_lat, "lng": fallback_lng}

@app.get("/api/orders")
def get_user_orders(user_id: int = Query(..., description="Telegram User ID")):
    if not client: return []
    db = client['shop_database']
    orders = list(db['orders'].find({"user_id": user_id}).sort("created_at", -1).limit(30))
    
    result = []
    for o in orders:
        receiver_address = o.get("address", "")
        recv_coords = guess_coordinates(receiver_address)

        raw_items = o.get("items", [])
        if not raw_items:
            raw_items = [{
                "link": o.get("product_link", "Đơn hàng cũ"),
                "carrier": o.get("carrier", "N/A"),
                "spx_code": o.get("spx_code", ""),
                "spx_stage": o.get("spx_stage", o.get("status", "")),
                "advance_payment": o.get("advance_payment", o.get("cod_amount", 0)),
                "tracking_history": o.get("tracking_history", [])
            }]

        items_data = []
        for item in raw_items:
            current_stage = item.get("spx_stage", o.get("status", ""))
            cur_coords = guess_coordinates(current_stage, fallback_lat=recv_coords["lat"] + 0.05, fallback_lng=recv_coords["lng"] + 0.05)

            items_data.append({
                "link": item.get("link", ""),
                "carrier": item.get("carrier", ""),
                "spx_code": item.get("spx_code", ""),
                "spx_stage": current_stage,
                "advance_payment": item.get("advance_payment", 0),
                "tracking_history": item.get("tracking_history", []),
                "current_lat": cur_coords["lat"],
                "current_lng": cur_coords["lng"],
                "receiver_lat": recv_coords["lat"],
                "receiver_lng": recv_coords["lng"],
            })
            
        result.append({
            "order_id": o.get("order_id", ""),
            "status": o.get("status", ""),
            "product_name": o.get("product_name", ""),
            "price": o.get("price", 0),
            "created_at": o["created_at"].strftime("%d/%m/%Y %H:%M") if "created_at" in o else "",
            "receiver_name": o.get("receiver_name", ""),
            "phone": o.get("phone", ""),
            "address": receiver_address,
            "note": o.get("note", ""),
            "items": items_data
        })
    return result

@app.get("/api/live_location")
def get_live_location(order_id: str = Query(...)):
    if not client: return {"lat": 19.3833, "lng": 105.7833, "status": "Không có kết nối"}
    db = client['shop_database']
    order = db['orders'].find_one({"order_id": order_id})
    if not order: return {"error": "Not found"}
        
    items = order.get("items", [])
    current_stage = items[0].get("spx_stage", order.get("status", "")) if items else order.get("status", "")
    cur_coords = guess_coordinates(current_stage)
    
    return {
        "lat": cur_coords["lat"],
        "lng": cur_coords["lng"],
        "status": current_stage,
        "status_full_address": current_stage
    }

@app.get("/api/stats")
def get_web_stats(user_id: int = Query(0)):
    if not client: return {"online": 1, "monthly": 1}
    db = client['shop_database']
    stats_col = db['web_stats']
    now = datetime.utcnow()
    current_month = now.strftime("%Y-%m")

    if user_id != 0:
        stats_col.update_one({"user_id": user_id, "month": current_month}, {"$set": {"last_active": now}}, upsert=True)

    three_mins_ago = now - timedelta(minutes=3)
    return {
        "online": max(1, stats_col.count_documents({"last_active": {"$gte": three_mins_ago}})),
        "monthly": max(1, stats_col.count_documents({"month": current_month}))
    }
