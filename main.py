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
import queue

from nocaptcha import captcha


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
REUSE_INTERVAL = config["behaviour"]["reuse_interval"]
BASE_URL = 'http://i.hzmbus.com/webh5api'
CAPTCHA_APP_ID = 'FFFF0N0000000000A95D'
CAPTCHA_SCENE = 'nc_other_h5'
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
        "19:00:00",
        "20:00:00"
    ],
    "ZHOHKG": [
        # "11:00:00",
        # "13:00:00",
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
        all_accounts = [line.split() for line in fp.read().strip().split('\n')]
    for account in all_accounts:
        username, password, active = account
        if bool(int(active)):
            yield Account(username=username, password=password)


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


def solve_captcha_1(session, headers):
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


def solve_captcha_2():
    while True:
        try:
            rsp = captcha(CAPTCHA_APP_ID, CAPTCHA_SCENE)
            data = json.loads(rsp)
            return {
                "sig": data["sig"],
                "token": data["token"],
                "sessionId": data["sessionId"]
            }
        except Exception as ex:
            error(ex)
            continue


class Worker:
    def __init__(self, account, send_back, complete):
        super().__init__()
        self.account = account
        self.send_back = send_back
        self.task = None
        self.complete = complete

        self.session = requests.session()

        self.headers = HEADERS.copy()
        self.headers['Cookie'] = get_cookies()
        self.headers.update({
            'Authorization': login(self.session, self.headers, self.account),
            'Referer': get_referrer()
        })

    def buy(self, date, slot, captcha_type):
        self.task = (date, slot, captcha_type)

        def create_body():
            passenger_info = get_passenger_info()
            body = with_base_body({
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
                "bookBeginTime": slot,
                "bookEndTime": slot,
                "sessionId": "",
                "sig": "",
                "token": "",
                "captcha": ""
            })
            if captcha_type == 1:
                body["captcha"] = solve_captcha_1(self.session, self.headers)
            elif captcha_type == 2:
                body.update(solve_captcha_2())
            return body

        while True:
            try:
                t = threading.Thread(
                    target=self.run_task,
                    kwargs={"body": create_body()}
                )
                logging.info(f"worker {self.account.username} started for {date} {slot} captcha-{captcha_type}")
                t.start()
                break
            except Exception as ex:
                logging.error(f"error occurred when creating worker {self.account.username}"
                              f" for {date} {slot} captcha-{captcha_type}")
                error(ex)

    def run_task(self, body):
        if BEGIN_TIME is None or time.time() > BEGIN_TIME:
            body["timestamp"] = int(time.time())
            try:
                rsp = self.session.post(f'{BASE_URL}/ticket/buy.ticket', headers=self.headers, data=json.dumps(body)).json()
                logging.info(
                    f"buying ticket for {self.account.username} captcha_type={1 if body['captcha'] else 2}: {rsp}")
                if rsp['code'] == 'SUCCESS':
                    send_email(self.account.username)
                    logging.info(f"已成功购买 for account: {self.account.username}")
                    return self.complete()
                elif rsp['code'] == 'FAIL':
                    if rsp['message'] == '您還有未支付的訂單,請先支付后再進行購票,謝謝!':
                        send_email(self.account.username)
                        return self.complete()

                self.send_back(*self.task)
            except Exception as ex:
                error(ex)
                self.send_back(*self.task)


def run():
    initialize_logger()
    date_range = get_date_range()
    # date_range = ["2022-12-18", "2022-12-17"]
    route = f"{FROM_STATION}{TO_STATION}"
    schedules = BUS_SCHEDULES[route]

    q = queue.Queue()
    completed = False

    def new_task(*args):
        logging.info(f"new task {args}")
        q.put(args)

    def complete():
        nonlocal completed
        completed = True

    for date in date_range:
        for slot in schedules:
            for captcha_type in (1, 2):
                new_task(date, slot, captcha_type)

    workers = {}
    accounts = list(get_accounts())
    i_accounts = 0

    while True:
        if completed:
            return
        date, slot, captcha_type = q.get()

        account = accounts[i_accounts]
        if account.username not in workers:
            def put_back(user_name):
                def fn(*args):
                    workers[user_name][2] = False
                    new_task(*args)
                return fn
            workers[account.username] = [Worker(account, put_back(account.username), complete), 0, True]

        worker, last_used, running = workers[account.username]
        current_time = time.time()
        if not running and current_time >= last_used + REUSE_INTERVAL:
            workers[account.username][2] = True
            workers[account.username][1] = int(current_time)
            threading.Thread(target=worker.buy, args=(date, slot, captcha_type)).start()

        i_accounts += 1
        i_accounts %= len(accounts)


if __name__ == '__main__':
    run()
