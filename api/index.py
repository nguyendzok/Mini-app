from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
import os
from datetime import datetime, timedelta

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URI = os.environ.get("MONGO_URI")
client = MongoClient(MONGO_URI) if MONGO_URI else None

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
            items_data.append({
                "link": item.get("link", ""),
                "carrier": item.get("carrier", ""),
                "spx_code": item.get("spx_code", ""),
                "spx_stage": item.get("spx_stage", ""),
                "advance_payment": item.get("advance_payment", 0),
                "tracking_history": item.get("tracking_history", [])
            })
            
        result.append({
            "order_id": o.get("order_id", ""),
            "status": o.get("status", ""),
            "product_name": o.get("product_name", ""),
            "price": o.get("price", 0),
            "created_at": o["created_at"].strftime("%d/%m/%Y %H:%M") if "created_at" in o else "",
            "receiver_name": o.get("receiver_name", ""),
            "phone": o.get("phone", ""),
            "address": o.get("address", ""),
            "note": o.get("note", ""),
            "items": items_data
        })
        
    return result

# ==========================================
# API MỚI: THEO DÕI NGƯỜI DÙNG REAL-TIME
# ==========================================
@app.get("/api/stats")
def get_web_stats(user_id: int = Query(0, description="Telegram User ID")):
    if not client:
        return {"online": 1, "monthly": 1}

    db = client['shop_database']
    stats_col = db['web_stats']
    
    now = datetime.utcnow()
    current_month = now.strftime("%Y-%m")

    # 1. Ghi nhận người dùng đang truy cập
    if user_id != 0:
        stats_col.update_one(
            {"user_id": user_id, "month": current_month},
            {"$set": {"last_active": now}},
            upsert=True
        )

    # 2. Đếm số người đang online (Có hoạt động trong 3 phút qua)
    three_mins_ago = now - timedelta(minutes=3)
    online_count = stats_col.count_documents({"last_active": {"$gte": three_mins_ago}})
    
    # 3. Đếm tổng truy cập duy nhất trong tháng hiện tại
    monthly_count = stats_col.count_documents({"month": current_month})

    # Giữ mức tối thiểu là 1 (chính là user đang xem)
    return {
        "online": max(1, online_count),
        "monthly": max(1, monthly_count)
    }
