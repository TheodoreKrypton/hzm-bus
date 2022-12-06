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
import sys
import time
import threading


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
BUS_SCHEDULES = {
    "HKGZHO": [
        "11:00:00",
        "12:00:00",
        "13:00:00",
        "14:00:00",
        "15:00:00",
        "16:00:00",
        "17:00:00",
        "18:00:00",
    ],
    "ZHOHKG": [
        "11:00:00",
        "13:00:00",
        "15:00:00",
        "16:00:00",
        "17:00:00",
    ]
}
TASKS_PER_ACCOUNT = 3
MAX_RETRY = 10
IMMEDIATE = True


@lru_cache(1)
def db():
    _db = pymysql.connect(**config["mysql"])
    return _db.cursor()


def query(sql):
    db().execute(sql)
    return db().fetchall()


def get_accounts():
    with open("accounts.txt", encoding='utf-8') as fp:
        all_accounts = [line.split() for line in fp.read().split('\n')]
    for account in all_accounts:
        username, password, active = account
        if bool(int(active)):
            yield Account(username=username, password=password)


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


def send_email(username):
    msg = MIMEText(f'请尽快上号支付 {username}', 'plain', 'utf-8')
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


@lru_cache(1)
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


def login(session, account):
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
                logging.info('登录成功 ' + account.username)
                return data["jwt"]
        except Exception as ex:
            logging.error(ex)


@lru_cache(1)
def get_date_range():
    begin_date = datetime.datetime.today().date()
    next_monday = begin_date + datetime.timedelta(days=(7 - begin_date.weekday()))
    return list(map(str, reversed([next_monday + datetime.timedelta(days=i) for i in range(0, 7)])))


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


def solve_captcha(session):
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


def buy_ticket(session, account, headers, body):
    body["timestamp"] = int(time.time())
    retry = 0
    while retry < MAX_RETRY:
        body["captcha"] = solve_captcha(session)
        rsp = session.post(f'{base_url}/ticket/buy.ticket', headers=headers, data=json.dumps(body)).json()
        logging.info(rsp)
        if rsp['code'] == 'SUCCESS':
            send_email(account.username)
            logging.info(f"已成功购买 for account: {account.username}")
            return True
        elif rsp['code'] == 'FAIL':
            if rsp['message'] == '您還有未支付的訂單,請先支付后再進行購票,謝謝!':
                send_email(account.username)
                return True
            return False
        retry += 1


class Worker(threading.Thread):
    def __init__(self, account, jobs):
        super().__init__()
        self.session = requests.session()
        self.jobs = jobs
        self.jwt = login(self.session, account)
        self.headers = HEADERS.copy()
        self.headers['Authorization'] = self.jwt
        self.headers['Referer'] = get_referrer()

        def create_body(date, _time):
            passenger_info = get_passenger_info()
            return with_base_body({
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
                "sessionId": "",
                "sig": "",
                "token": "",
            })

        self.threads = [threading.Thread(
            target=buy_ticket,
            args=(self.session, account, self.headers, create_body(*job))
        ) for job in jobs]

    def run(self) -> None:
        while True:
            t = time.localtime()
            if IMMEDIATE or (t.tm_hour == 19 and t.tm_min == 59 and t.tm_sec == 59):
                for thread in self.threads:
                    thread.start()

                for thread in self.threads:
                    thread.join()

                break


def run():
    initialize_logger()
    set_cookies()
    # date_range = get_date_range()
    date_range = ["2022-12-08", "2022-12-07"]
    route = f"{from_station}{to_station}"
    schedules = BUS_SCHEDULES[route]

    buffer = []
    threads = []

    try:
        for date in date_range:
            for slot in schedules:
                buffer.append((date, slot))
                if len(buffer) == TASKS_PER_ACCOUNT:
                    account = next(accounts)
                    threads.append(Worker(account, buffer))
                    buffer = []

        account = next(accounts)
        threads.append(Worker(account, buffer))
    except StopIteration:
        pass

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()


run()
