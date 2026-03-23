from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
import os
import re
import requests
from datetime import datetime, timedelta
import traceback

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URI = os.environ.get("MONGO_URI", "mongodb+srv://DÁN_LINK_MONGODB_CỦA_BẠN_VÀO_ĐÂY_NẾU_CẦN")
client = MongoClient(MONGO_URI) if MONGO_URI else None

HUB_LOCATIONS = {
    "thâm quyến": {"lat": 22.5431, "lng": 114.0579},
    "nghĩa ô": {"lat": 29.3068, "lng": 120.0750},
    "bw soc": {"lat": 11.0067, "lng": 106.5139},
    "hn mê linh soc": {"lat": 21.1828, "lng": 105.7142},
    "từ sơn soc": {"lat": 21.1167, "lng": 105.9500},
    "tĩnh gia 2": {"lat": 19.3833, "lng": 105.7833},
}

def extract_gmap_coords(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5, allow_redirects=True)
        final_url = res.url
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', final_url)
        if match: return {"lat": float(match.group(1)), "lng": float(match.group(2))}
    except: pass
    return None

def guess_coordinates(text, fallback_lat=19.3833, fallback_lng=105.7833):
    if not text: return {"lat": fallback_lat, "lng": fallback_lng}
    try:
        text_lower = str(text).lower().strip()
        if "http" in text_lower and ("maps" in text_lower or "goo.gl" in text_lower):
            url_match = re.search(r'(https?://[^\s]+)', text)
            if url_match:
                coords = extract_gmap_coords(url_match.group(1))
                if coords: return coords
        for key, coords in HUB_LOCATIONS.items():
            if key in text_lower: return coords
    except: pass
    return {"lat": fallback_lat, "lng": fallback_lng}

@app.get("/api/orders")
def get_user_orders(user_id: str = Query(...)):
    try:
        if not client: 
            return JSONResponse(status_code=500, content={"detail": "Chưa kết nối được với MongoDB. Hãy kiểm tra lại MONGO_URI."})
            
        db = client['shop_database']
        try: uid = int(user_id)
        except: uid = user_id
        
        orders = list(db['orders'].find({"$or": [{"user_id": uid}, {"user_id": str(user_id)}]}).sort("created_at", -1).limit(30))
        result = []
        
        for o in orders:
            receiver_address = str(o.get("address", ""))
            recv_coords = guess_coordinates(receiver_address)

            raw_items = o.get("items")
            if not isinstance(raw_items, list): raw_items = []
            if len(raw_items) == 0:
                raw_items = [{
                    "link": o.get("product_link", "Đơn hàng"),
                    "carrier": o.get("carrier", "N/A"),
                    "spx_code": o.get("spx_code", ""),
                    "spx_stage": o.get("spx_stage", o.get("status", "")),
                    "advance_payment": o.get("advance_payment", 0),
                    "tracking_history": []
                }]

            items_data = []
            for item in raw_items:
                if not isinstance(item, dict): continue 
                
                current_stage = str(item.get("spx_stage") or o.get("status") or "Đang xử lý")
                cur_coords = guess_coordinates(current_stage, fallback_lat=recv_coords["lat"] + 0.05, fallback_lng=recv_coords["lng"] + 0.05)
                
                # Làm sạch lịch sử tracking để tránh lỗi JSON Serialize
                t_history = item.get("tracking_history")
                safe_history = []
                if isinstance(t_history, list):
                    for h in t_history:
                        if isinstance(h, dict):
                            safe_history.append({
                                "time": str(h.get("time", "")),
                                "description": str(h.get("description", ""))
                            })
                            
                try: adv_pay = float(item.get("advance_payment", 0))
                except: adv_pay = 0

                items_data.append({
                    "link": str(item.get("link", "")),
                    "carrier": str(item.get("carrier", "")),
                    "spx_code": str(item.get("spx_code", "")),
                    "spx_stage": current_stage,
                    "advance_payment": adv_pay,
                    "tracking_history": safe_history,
                    "current_lat": float(cur_coords["lat"]),
                    "current_lng": float(cur_coords["lng"]),
                    "receiver_lat": float(recv_coords["lat"]),
                    "receiver_lng": float(recv_coords["lng"]),
                })
                
            dt = o.get("created_at")
            if isinstance(dt, datetime): dt_str = dt.strftime("%d/%m/%Y %H:%M")
            else: dt_str = str(dt) if dt else datetime.now().strftime("%d/%m/%Y %H:%M")
            
            try: price_val = float(o.get("price", 0))
            except: price_val = 0
                
            result.append({
                "order_id": str(o.get("order_id", "")),
                "status": str(o.get("status", "Đang xử lý")),
                "product_name": str(o.get("product_name", "Đơn hàng mới")),
                "price": price_val,
                "created_at": dt_str,
                "receiver_name": str(o.get("receiver_name", "")),
                "phone": str(o.get("phone", "")),
                "address": receiver_address,
                "items": items_data
            })
                
        return result
    except Exception as e:
        error_msg = traceback.format_exc()
        print(f"LỖI NGHIÊM TRỌNG API ORDERS:\n{error_msg}")
        # TRẢ THẲNG LỖI VỀ CHO MINI APP HIỂN THỊ
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.get("/api/live_location")
def get_live_location(order_id: str = Query(...)):
    try:
        if not client: return {"lat": 19.3833, "lng": 105.7833, "status": "Không có kết nối"}
        db = client['shop_database']
        order = db['orders'].find_one({"order_id": order_id})
        if not order: return {"error": "Not found"}
            
        items = order.get("items") or []
        current_stage = order.get("status", "")
        if isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
            current_stage = items[0].get("spx_stage", order.get("status", ""))
            
        cur_coords = guess_coordinates(current_stage)
        return {"lat": cur_coords["lat"], "lng": cur_coords["lng"], "status": current_stage}
    except:
        return {"lat": 19.3833, "lng": 105.7833, "status": "Lỗi cập nhật"}

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
