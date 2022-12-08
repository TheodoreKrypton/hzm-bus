import json
import smtplib
import urllib.parse
import requests
from selenium import webdriver
from email.mime.text import MIMEText
from email.header import Header
from functools import lru_cache
import logging
from dataclasses import dataclass
import datetime
import ddddocr
import sys
import time
import threading
import traceback


@dataclass
class Account:
    username: str
    password: str


@dataclass
class Passenger:
    name: str
    idcard: str


config = json.load(open('config.json', 'r', encoding='utf-8'))
FROM_STATION = config["ticket"]["from"]
TO_STATION = config["ticket"]["to"]
BEGIN_TIME = config["behaviour"]["begin_time"]
TASKS_PER_ACCOUNT = config["behaviour"]["tasks_per_account"]
MAX_RETRY = config["behaviour"]["max_retry"]
BASE_URL = 'http://i.hzmbus.com/webh5api'
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

ocr = ddddocr.DdddOcr()


def error(ex):
    logging.error(''.join(traceback.format_tb(ex.__traceback__)))


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
        "version": "2.7.2032.1262",
        "equipment": "PC"
    }
    base_body.update(addition)
    return base_body


def initialize_logger():
    logging.basicConfig(
        filename='hzmbus.log',
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
        "xlmc_1": FROM_STATION,
        "xlmc_2": TO_STATION,
        "xllb": 1,
        "xldm": f"{FROM_STATION}{TO_STATION}",
        "code_1": FROM_STATION,
        "code_2": TO_STATION
    })
    return f"https://i.hzmbus.com/webhtml/ticket_details?{params}"


def get_driver():
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
    driver.get('chrome://settings/clearBrowserData')
    return driver


def get_cookies():
    driver = get_driver()
    driver.get(f"{BASE_URL}/login")

    while True:
        try:
            cookies = driver.get_cookies()
            if 'PHPSESSID' in str(cookies):
                cookies = ';'.join((f"{cookie['name']}={cookie['value']}" for cookie in cookies))
                driver.close()
                return cookies
        except Exception as ex:
            error(ex)


def login(session, headers, account):
    user = with_base_body({
        "webUserid": account.username,
        "passWord": account.password,
        "code": ""
    })

    while True:
        try:
            rsp = session.post(url=f"{BASE_URL}/login", data=json.dumps(user), headers=headers, verify=False)
            data = rsp.json()
            if data['code'] == 'SUCCESS':
                logging.info('登录成功 ' + account.username)
                return data["jwt"]
        except Exception as ex:
            error(ex)


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


def solve_captcha(session, headers):
    while True:
        try:
            captcha = session.get(f"{BASE_URL}/captcha?1", headers=headers)
            res = ocr.classification(captcha.content)
            if len(res) != 4:
                continue
            try:
                int(res)
                return res
            except ValueError:
                continue
        except Exception as ex:
            error(ex)


def buy_ticket(session, account, headers, body):
    retry = 0
    while retry < MAX_RETRY:
        body["timestamp"] = int(time.time())
        try:
            rsp = session.post(f'{BASE_URL}/ticket/buy.ticket', headers=headers, data=json.dumps(body)).json()
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
        except Exception as ex:
            error(ex)
        retry += 1
        body["captcha"] = solve_captcha(session, headers)


class Worker(threading.Thread):
    def __init__(self, account, jobs):
        super().__init__()
        self.jobs = jobs

        def create_body(_session, _headers, date, _time):
            passenger_info = get_passenger_info()
            return with_base_body({
                "ticketData": date,
                "lineCode": f'{FROM_STATION}{TO_STATION}',
                "startStationCode": FROM_STATION,
                "endStationCode": TO_STATION,
                "boardingPointCode": f"{FROM_STATION}01",
                "breakoutPointCode": f"{TO_STATION}01",
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
                "captcha": solve_captcha(_session, _headers)
            })

        self.threads = []
        for job in jobs:
            while True:
                try:
                    session = requests.session()

                    headers = HEADERS.copy()
                    headers['Cookie'] = get_cookies()
                    headers.update({
                        'Authorization': login(session, headers, account),
                        'Referer': get_referrer()
                    })
                    thread = threading.Thread(
                        target=self.run_task,
                        args=(session, account, headers, create_body(session, headers, *job))
                    )
                    logging.info(f"worker {account.username} started for {' '.join(job)}")
                    self.threads.append(thread)
                    break
                except Exception as ex:
                    logging.error(f"creating worker {account.username} for {' '.join(job)} error: {fmt_ex(ex)}")
                    error(ex)

    @staticmethod
    def run_task(session, account, headers, body):
        while True:
            if BEGIN_TIME is None or time.time() > BEGIN_TIME:
                buy_ticket(session, account, headers, body)
                break

    def run(self) -> None:
        for thread in self.threads:
            thread.start()

        for thread in self.threads:
            thread.join()


def run():
    initialize_logger()
    date_range = get_date_range()
    # date_range = ["2022-12-08", "2022-12-07"]
    route = f"{FROM_STATION}{TO_STATION}"
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
