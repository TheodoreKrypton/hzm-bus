from __future__ import print_function

import os.path
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import requests

import re

from main import get_cookies

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']


"""Shows basic usage of the Gmail API.
Lists the user's Gmail labels.
"""
creds = None
# The file token.json stores the user's access and refresh tokens, and is
# created automatically when the authorization flow completes for the first
# time.
if os.path.exists('token.json'):
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
# If there are no (valid) credentials available, let the user log in.
if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(
            'credentials.json', SCOPES)
        creds = flow.run_local_server(port=0)
    # Save the credentials for the next run
    with open('token.json', 'w') as token:
        token.write(creds.to_json())

service = build('gmail', 'v1', credentials=creds)
BASE_URL = "https://i.hzmbus.com/webh5api"


last_id = ""


def get_captcha():
    global last_id
    while True:
        results = service.users().messages().list(userId='me', labelIds=['INBOX'], q="from:ticketsystem@hzmbus.com").execute()
        email_id = results['messages'][0]['id']
        if email_id != last_id:
            last_id = email_id
            data = service.users().messages().get(userId='me', id=results['messages'][0]['id']).execute()
            captcha = re.search(r'(?<=验证码：)\d+', data['snippet']).group(0)
            return captcha
        time.sleep(1)


cookie = get_cookies()
def send_captcha(email):
    body = {"email":email,"appId":"HZMBWEB_HK","joinType":"WEB","version":"2.7.2032.1262","equipment":"PC"}
    rsp = requests.post(f"{BASE_URL}/web/query.web.verification.code", headers={'Cookie': cookie}, json=body)
    return rsp.json()['code'] == "SUCCESS"


def signup(email, password, captcha):
    body = {"email":email,"webUserid":email,"passWord":password,"verificationCode":captcha,"code":"","appId":"HZMBWEB_HK","joinType":"WEB","version":"2.7.2032.1262","equipment":"PC"}
    rsp = requests.post(f"{BASE_URL}/wx/wx.user.register", headers={'Cookie': cookie}, json=body)
    print(rsp.json())
    return rsp.json()['code'] == "SUCCESS"


get_captcha()

for i in range(10, 2000):
    new_email = f"{i}@"
    while True:
        send_captcha(new_email)
        captcha = get_captcha()
        status = signup(new_email, "", captcha)
        if status:
            break
        print(f"{new_email} 注册失败，重试")
    print(f"{new_email} 注册成功")
