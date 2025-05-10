import os
import re
import time
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

BASE_URL = os.getenv("BASE_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_GROUP_ID = os.getenv("LINE_GROUP_ID")


def send_line_message(message):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "to": LINE_GROUP_ID,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }
    response = requests.post(url, headers=headers, json=data)
    print("LINE送信レスポンス:", response.status_code, response.text)
    return response.status_code == 200


def init_driver():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    return webdriver.Chrome(options=options)


def get_park_list(driver, date):
    driver.get(BASE_URL)
    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.ID, "daystart-home")))
    date_input = driver.find_element(By.ID, "daystart-home")
    date_str = date.strftime("%Y-%m-%d")
    driver.execute_script(f"arguments[0].value = '{date_str}'", date_input)
    purpose_select = driver.find_element(By.ID, "purpose-home")
    for option in purpose_select.find_elements(By.TAG_NAME, "option"):
        if option.text.strip() == "野球":
            option.click()
            break
    bname_select = wait.until(EC.element_to_be_clickable((By.ID, "bname-home")))
    park_options = bname_select.find_elements(By.TAG_NAME, "option")
    park_list = [
        {"name": option.text.strip(), "value": option.get_attribute("value")}
        for option in park_options if option.get_attribute("value") != "0"
    ]
    print(f"公園数: {len(park_list)}")
    return park_list


def parse_week_table(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "week-info"})
    if not table:
        return []
    thead = table.find("thead")
    date_list = []
    if thead:
        ths = thead.find_all("th")[1:]
        for th in ths:
            divs = th.find_all("div")
            if len(divs) >= 2:
                date_span = divs[0].find_all("span")
                date_str = ""
                for span in date_span:
                    if span.get_text(strip=True).isdigit():
                        date_str = span.get_text(strip=True)
                        break
                day_span = divs[1].find_all("span", class_="pc-text")
                day_str = day_span[0].get_text(strip=True) if day_span else ""
                if day_str in ["土", "日"]:
                    date_list.append(f"{date_str}({day_str})")
                else:
                    date_list.append("")
            else:
                date_list.append("")
    tbody = table.find("tbody")
    if not tbody:
        return []
    rows = tbody.find_all("tr")
    time_list = []
    cell_matrix = []
    for row in rows:
        cells = row.find_all("td")
        th = row.find("th")
        time = th.get_text(strip=True) if th else ""
        time_list.append(time)
        cell_matrix.append(cells)
    result = []
    for col in range(len(date_list)):
        if date_list[col]:
            for row in range(len(time_list)):
                cell = cell_matrix[row][col]
                img = cell.find("img", alt="空き")
                if img:
                    span = cell.find("span")
                    status = span.get_text(strip=True) if span else "空き"
                    result.append({
                        "date": date_list[col],
                        "time": time_list[row],
                        "status": status,
                    })
    return result


def get_park_availability(driver, park, wait):
    print(f"検索開始")
    bname_select = wait.until(EC.element_to_be_clickable((By.ID, "bname-home")))
    for option in bname_select.find_elements(By.TAG_NAME, "option"):
        if option.get_attribute("value") == park['value']:
            option.click()
            break
    search_btn = wait.until(EC.element_to_be_clickable((By.ID, "btn-go")))
    before_url = driver.current_url
    search_btn.click()
    WebDriverWait(driver, 10).until(lambda d: d.current_url != before_url)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.calendar")))
    print(f"テーブルの表示")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table#week-info tbody tr")))
    week_results = []
    for week in range(4):
        week_results += parse_week_table(driver.page_source)
        if week < 3:
            table_html = driver.find_element(By.CSS_SELECTOR, "table#week-info").get_attribute("outerHTML")
            next_btn = wait.until(EC.element_to_be_clickable((By.ID, "next-week")))
            driver.execute_script("arguments[0].click();", next_btn)
            WebDriverWait(driver, 10).until(lambda d: d.find_element(By.ID, "week-info").get_attribute("outerHTML") != table_html)
            print(f"{week+2}周目への遷移")
    print(f"情報取得完了")
    return week_results


def time_sort_key(slot):
    m = re.match(r"([０-９0-9]+)", slot["time"])
    if m:
        zenkaku = str.maketrans('０１２３４５６７８９', '0123456789')
        t = m.group(1).translate(zenkaku)
        return int(t)
    return 0


def make_line_message(park_results):
    lines = ["📣 グラウンドの空き情報\n"]
    for park_name, slots in park_results.items():
        if not slots:
            continue
        lines.append(f"【{park_name}】")
        for slot in sorted(slots, key=lambda x: (x["date"], time_sort_key(x))):
            m = re.match(r"(\d+)[(（](.)[)）]", slot["date"])
            if m:
                day = m.group(1)
                youbi = m.group(2)
            else:
                day = slot["date"]
                youbi = ""
            time_hankaku = slot['time'].translate(str.maketrans('０１２３４５６７８９', '0123456789'))
            lines.append(f"・{day}日({youbi}) {time_hankaku}：{slot['status']}枠")
        lines.append("")
    return "\n".join(lines)


def main():
    today = datetime.today()
    driver = init_driver()
    wait = WebDriverWait(driver, 20)
    try:
        park_list = get_park_list(driver, today)
        park_results = {}
        for park in park_list:
            try:
                print(f"\n【{park['name']}】の空き枠検索開始")
                slots = get_park_availability(driver, park, wait)
                park_results[park['name']] = slots
            except Exception as e:
                print(f"公園「{park['name']}」の処理中にエラー: {str(e)}")
                continue
            finally:
                driver.get(BASE_URL)
                wait.until(EC.presence_of_element_located((By.ID, "daystart-home")))
                print(f"ホーム画面に戻る")
                time.sleep(3)
        message = make_line_message(park_results)
        send_line_message(message)
        print('LINE:')
        print(message)
    finally:
        driver.quit()
        print("\n検索完了")


def main_with_retry(max_retries=5):
    for attempt in range(1, max_retries + 1):
        try:
            main()
            break
        except Exception as e:
            print(f"[リトライ] {attempt}回目でエラー発生: {repr(e)}")
            if attempt == max_retries:
                print("最大リトライ回数に達したため処理を中断します。")
            else:
                print("再実行します...")

if __name__ == "__main__":
    main_with_retry(max_retries=5) 