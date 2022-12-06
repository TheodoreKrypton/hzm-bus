import json
import smtplib
import urllib.parse
import requests
from selenium import webdriver
from email.mime.text import MIMEText
from email.header import Header
from functools import lru_cache
import pymysql
import logging
from dataclasses import dataclass
import datetime
import ddddocr
from typing import Optional
import sys
import time


@dataclass
class Account:
    username: str
    password: str


@dataclass
class Passenger:
    name: str
    idcard: str


config = json.load(open('config.json', 'r', encoding='utf-8'))
from_station = config["ticket"]["from"]
to_station = config["ticket"]["to"]
session = requests.session()
current_account = None  # type: Optional[Account]
base_url = 'http://i.hzmbus.com/webh5api'
HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
    'Connection': 'keep-alive',
    'Content-Type': 'application/json;charset=UTF-8',
    'Host': 'i.hzmbus.com',
    'Origin': 'https://i.hzmbus.com',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'sec-ch-ua': '"Not?A_Brand";v="8", "Chromium";v="108", "Google Chrome";v="108"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
}
ocr = ddddocr.DdddOcr()


@lru_cache(1)
def db():
    _db = pymysql.connect(**config["mysql"])
    return _db.cursor()


def query(sql):
    db().execute(sql)
    return db().fetchall()


def get_accounts():
    global current_account

    with open("accounts.txt", encoding='utf-8') as fp:
        all_accounts = [line.split() for line in fp.read().split('\n')]
    i = 0
    while True:
        i %= len(all_accounts)
        username, password, active = all_accounts[i]
        i += 1
        if bool(int(active)):
            current_account = Account(username=username, password=password)
            yield current_account


accounts = get_accounts()


@lru_cache(1)
def get_passengers():
    return [Passenger(name=psg["name"], idcard=psg["idcard"]) for psg in config["passengers"]]


def with_base_body(addition):
    base_body = {
        "appId": "HZMBWEB_HK",
        "joinType": "WEB",
        "version": "2.7.202207.1213",
        "equipment": "PC"
    }
    base_body.update(addition)
    return base_body


def initialize_logger():
    logging.basicConfig(
        filename='hzmbus.log',
        encoding='utf-8',
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [line:%(lineno)d] - %(message)s',
    )
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))


def send_email():
    msg = MIMEText(f'请尽快上号支付 {current_account.username}', 'plain', 'utf-8')
    msg['From'] = Header(config["email"]["from"]["address"])
    msg['To'] = Header(config["email"]["to"])
    subject = '中了'
    msg['Subject'] = Header(subject, 'utf-8')
    try:
        mailserver = smtplib.SMTP(config["email"]["from"]["smtp"], 587)
        mailserver.ehlo()
        mailserver.starttls()
        mailserver.login(config["email"]["from"]["address"], config["email"]["from"]["password"])
        mailserver.sendmail(config["email"]["from"]["address"], config["email"]["to"], msg.as_string())
    finally:
        try:
            mailserver.quit()
        except Exception:
            pass


code = {
    "HKG": "香港",
    "ZHO": "珠海"
}


@lru_cache(0)
def get_referrer():
    params = urllib.parse.urlencode({
        "xlmc_1": from_station,
        "xlmc_2": to_station,
        "xllb": 1,
        "xldm": f"{from_station}{to_station}",
        "code_1": from_station,
        "code_2": to_station
    })
    return f"https://i.hzmbus.com/webhtml/ticket_details?{params}"


def set_cookies():
    options = webdriver.ChromeOptions()
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',
                           {'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'})
    driver.get(f"{base_url}/login")

    while True:
        cookies = driver.get_cookies()
        if 'PHPSESSID' in str(cookies):
            cookies = ';'.join((f"{cookie['name']}={cookie['value']}" for cookie in cookies))
            HEADERS['Cookie'] = cookies
            break
    driver.close()


def switch_account():
    logging.info("switching account")
    account = next(accounts)
    user = with_base_body({
        "webUserid": account.username,
        "passWord": account.password,
        "code": ""
    })

    while True:
        try:
            rsp = session.post(url=f"{base_url}/login", data=json.dumps(user), headers=HEADERS, verify=False)
            data = rsp.json()
            if data['code'] == 'SUCCESS':
                logging.info('登录成功 ' + str(current_account.username))
                HEADERS['Authorization'] = data["jwt"]
                return
        except Exception as ex:
            logging.error(ex)


def get_ticket_info(date):
    while True:
        try:
            body = with_base_body({
                "bookDate": str(date),
                "lineCode": f"{from_station}{to_station}"
            })
            book_info = session.post(f'{base_url}/manage/query.book.info.data', data=json.dumps(body), headers=HEADERS)
            if book_info.json()['code'] != 'SUCCESS':
                logging.error(book_info.json()['message'])
                switch_account()
                continue
            return book_info.json()['responseData']
        except Exception as error:
            logging.error(f'访问异常, {error}')
            set_cookies()
            switch_account()
            continue


def get_date_range():
    begin_date = datetime.datetime.now().date()
    while True:
        rsp = get_ticket_info(begin_date)
        if not rsp:
            switch_account()
        else:
            end_date = rsp[0]['maxBookDate']
            break
    date_range = []
    while True:
        date_range.append(str(begin_date))
        begin_date += datetime.timedelta(days=1)
        if str(begin_date) == end_date:
            date_range.append(end_date)
            break
    return date_range


@lru_cache(1)
def get_passenger_info():
    passengers = get_passengers()
    if f"{config['ticket']['from']}{config['ticket']['to']}" == "HKGZHO":
        return [{
            "ticketType": "00",
            "idCard": passenger.idcard,
            "idType": 1,
            "userName": passenger.name,
            "telNum": ""
        } for passenger in passengers]
    return [{
        "ticketType": "00",
        "idCard": "",
        "idType": 1,
        "userName": "",
        "telNum": ""
    }] * len(passengers)


def ticket_query(date_range):
    for date in date_range:
        logging.info(f'当前查询日期[{date}]')
        get_book_info = get_ticket_info(date)
        if not get_book_info:
            switch_account()
            continue
        seats_available = 0
        passengers = get_passengers()
        for item in get_book_info:
            try:
                seats_available += int(item["maxPeople"])
                if seats_available >= len(passengers):
                    logging.info(f'时间: {date} {item["beginTime"]}, 票数: {seats_available}, '
                                 f'状态: {"不能购买" if seats_available <= len(passengers) else "正在购买"}')
                    return buy_ticket(date, item["beginTime"])
            except:
                logging.error('当日无车票信息')
        logging.info(f'时间: {date}, 票数: {seats_available}, 状态: {"不能购买" if seats_available == 0 else "可以购买"}')


def solve_captcha():
    while True:
        captcha = session.get(f"{base_url}/captcha?1", headers=HEADERS)
        res = ocr.classification(captcha.content)
        with open("captcha.jpg", "wb") as fp:
            fp.write(captcha.content)
        if len(res) != 4:
            continue
        try:
            int(res)
            return res
        except ValueError:
            continue


def buy_ticket(date, _time):
    passenger_info = get_passenger_info()
    body = with_base_body({
        "ticketData": date,
        "lineCode": f'{from_station}{to_station}',
        "startStationCode": from_station,
        "endStationCode": to_station,
        "boardingPointCode": f"{from_station}01",
        "breakoutPointCode": f"{to_station}01",
        "currency": "2",
        "ticketCategory": "1",
        "tickets": passenger_info,
        "amount": 6500 * len(passenger_info),
        "feeType": 9,
        "totalVoucherpay": 0,
        "voucherNum": 0,
        "voucherStr": "",
        "totalBalpay": 0,
        "totalNeedpay": 6500 * len(passenger_info),
        "bookBeginTime": _time,
        "bookEndTime": _time,
        "captcha": solve_captcha(),
        "sessionId": "",
        "sig": "",
        "token": "",
        "timestamp": int(time.time())
    })
    new_headers = HEADERS.copy()
    new_headers['Referer'] = get_referrer()

    while True:
        rsp = session.post(f'{base_url}/ticket/buy.ticket', headers=HEADERS, data=json.dumps(body)).json()
        logging.info(rsp)
        if rsp['code'] == 'SUCCESS':
            send_email()
            logging.info(f"已成功购买 for account: {current_account.username}")
            return True
        elif rsp['code'] == 'FAIL':
            if rsp['message'] == '您還有未支付的訂單,請先支付后再進行購票,謝謝!':
                send_email()
                return True
            return False
        else:
            switch_account()


def run():
    initialize_logger()
    set_cookies()
    switch_account()
    date_range = get_date_range()
    while True:
        if ticket_query(date_range):
            break
        switch_account()


run()
