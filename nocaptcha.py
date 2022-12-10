import requests
import time
import random

url = "/nocaptcha/initialize.jsonp"
app_key = "FFFF0N0000000000A95D"


params = {
    "a": app_key,
    "t": f"{app_key}:Anc_other_h5:{time.time_ns() // 100000}:0.20349638020506866",
    "scene": "nc_other_h5",
    "lang": "en",
    "v": "1.2.20",
    "href": "https://i.hzmbus.com/webhtml/ticket_details",
    "comm": "{}",
    "callback": "initializeJsonp_035387605247055376"
}

rsp = requests.get(url, params=params)
print(rsp.text)
