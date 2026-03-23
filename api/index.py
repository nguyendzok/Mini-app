from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
import os
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

# BỘ TỪ ĐIỂN TỌA ĐỘ CÁC TRẠM TRUNG CHUYỂN / ĐỊA PHƯƠNG
HUB_LOCATIONS = {
    "từ sơn": {"lat": 21.1167, "lng": 105.9500},
    "trang hạ": {"lat": 21.1218, "lng": 105.9405},
    "hà nội": {"lat": 21.0285, "lng": 105.8542},
    "hoàn kiếm": {"lat": 21.0285, "lng": 105.8542},
    "mê linh": {"lat": 21.1828, "lng": 105.7142},
    "thanh hóa": {"lat": 19.8056, "lng": 105.7766},
    "trường thpt tĩnh gia 2": {"lat": 19.3833, "lng": 105.7833},
    "tĩnh gia 2": {"lat": 19.3833, "lng": 105.7833},
    "hồ chí minh": {"lat": 10.8231, "lng": 106.6297},
    "củ chi": {"lat": 11.0067, "lng": 106.5139},
    "đà nẵng": {"lat": 16.0471, "lng": 108.2062}
}

def guess_coordinates(text, fallback_lat=21.0285, fallback_lng=105.8542):
    if not text:
        return {"lat": fallback_lat, "lng": fallback_lng}
    text_lower = str(text).lower()
    for key, coords in HUB_LOCATIONS.items():
        if key in text_lower:
            return coords
    return {"lat": fallback_lat, "lng": fallback_lng}

@app.get("/api/orders")
def get_user_orders(user_id: int = Query(..., description="Telegram User ID")):
    if not client:
        return []

    db = client['shop_database']
    orders_col = db['orders']
    
    cursor = orders_col.find({"user_id": user_id}).sort("created_at", -1).limit(30)
    orders = list(cursor)
    
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
            
            # Đoán tọa độ hiện tại
            cur_coords = guess_coordinates(
                current_stage, 
                fallback_lat=recv_coords["lat"] + 0.08, 
                fallback_lng=recv_coords["lng"] + 0.08
            )

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
    if not client:
        return {"lat": 21.0285, "lng": 105.8542, "status": "Không có kết nối"}

    db = client['shop_database']
    orders_col = db['orders']
    
    order = orders_col.find_one({"order_id": order_id})
    if not order:
        return {"error": "Not found"}
        
    items = order.get("items", [])
    if items:
        current_stage = items[0].get("spx_stage", order.get("status", ""))
    else:
        current_stage = order.get("status", "")

    cur_coords = guess_coordinates(current_stage)
    
    return {
        "lat": cur_coords["lat"],
        "lng": cur_coords["lng"],
        "status": current_stage,
        "status_full_address": current_stage
    }

@app.get("/api/stats")
def get_web_stats(user_id: int = Query(0, description="Telegram User ID")):
    if not client:
        return {"online": 1, "monthly": 1}

    db = client['shop_database']
    stats_col = db['web_stats']
    
    now = datetime.utcnow()
    current_month = now.strftime("%Y-%m")

    if user_id != 0:
        stats_col.update_one(
            {"user_id": user_id, "month": current_month},
            {"$set": {"last_active": now}},
            upsert=True
        )

    three_mins_ago = now - timedelta(minutes=3)
    online_count = stats_col.count_documents({"last_active": {"$gte": three_mins_ago}})
    monthly_count = stats_col.count_documents({"month": current_month})

    return {
        "online": max(1, online_count),
        "monthly": max(1, monthly_count)
    }
