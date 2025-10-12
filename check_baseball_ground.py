import os
import re
import time
import base64
import requests
from PIL import Image
from dotenv import load_dotenv
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

load_dotenv()
BASE_URL = os.getenv("BASE_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_GROUP_ID = os.getenv("LINE_GROUP_ID")
GAME_RECRUITMENT_URL = os.getenv("GAME_RECRUITMENT_URL")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # 形式: "username/repository"
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")  # デフォルトはmain

def commit_and_push_screenshot(screenshot_path):
    """スクリーンショットをGitにコミット＆プッシュ"""
    try:
        import subprocess
        
        print(f"\nGitにスクリーンショットをコミット: {screenshot_path}")
        
        # git add
        subprocess.run(["git", "add", screenshot_path], check=True)
        
        # git commit
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        commit_message = f"Add screenshot: {os.path.basename(screenshot_path)} at {timestamp}"
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        
        # git push
        subprocess.run(["git", "push"], check=True)
        
        print("Gitプッシュ完了")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"Gitコマンドエラー: {str(e)}")
        return False
    except Exception as e:
        print(f"Git操作中にエラー発生: {str(e)}")
        return False


def get_github_raw_url(screenshot_path):
    """スクリーンショットのGitHub Raw URLを生成"""
    if not GITHUB_REPO:
        print("GITHUB_REPOが設定されていません")
        return None
    
    # ファイル名とディレクトリ名を取得
    filename = os.path.basename(screenshot_path)
    
    # GitHub RawのURL形式: https://raw.githubusercontent.com/{user}/{repo}/{branch}/screenshots/{filename}
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/screenshots/{filename}"
    
    print(f"GitHub Raw URL: {raw_url}")
    return raw_url


def send_line_message(message, screenshot_path=None):
    print("\nLINEメッセージ送信開始")
    
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_GROUP_ID:
        print("LINE APIの設定が不完全です")
        return False
    
    # メッセージの配列を作成
    messages = [{"type": "text", "text": message}]
    
    # 画像がある場合はGitHubにプッシュしてURLを生成
    if screenshot_path and os.path.exists(screenshot_path):
        # Gitにコミットしてプッシュ
        if commit_and_push_screenshot(screenshot_path):
            # GitHub RawのURLを生成
            image_url = get_github_raw_url(screenshot_path)
            
            if image_url:
                messages.append({
                    "type": "image",
                    "originalContentUrl": image_url,
                    "previewImageUrl": image_url
                })
                print("画像メッセージを追加しました")
            else:
                print("GitHub URLの生成に失敗したため、テキストのみ送信します")
        else:
            print("Gitプッシュに失敗したため、テキストのみ送信します")
    
    # LINEに送信
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "to": LINE_GROUP_ID,
        "messages": messages
    }
    
    response = requests.post(url, headers=headers, json=data)
    print(f"LINE送信結果: {response.status_code}")
    
    if response.status_code != 200:
        print(f"LINE送信エラー: {response.text}")
    
    return response.status_code == 200


def init_driver():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,3000')  # 縦長のウィンドウサイズ
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

# スクリーンショットの保存先ディレクトリ
SCREENSHOT_DIR = "screenshots"
if not os.path.exists(SCREENSHOT_DIR):
    os.makedirs(SCREENSHOT_DIR)

def get_game_recruitment_screenshot(driver):
    if not GAME_RECRUITMENT_URL:
        print("GAME_RECRUITMENT_URLが設定されていないためスキップ")
        return None, None
    try:
        print(f"\n対戦相手募集ページにアクセス: {GAME_RECRUITMENT_URL}")
        driver.get(GAME_RECRUITMENT_URL)
        wait = WebDriverWait(driver, 10)
        
        # ログインフォームの要素を待機
        userid_input = wait.until(EC.presence_of_element_located((By.ID, "userid")))
        password_input = driver.find_element(By.ID, "password")
        
        # 環境変数からログイン情報を取得
        login_id = os.getenv("MANAGEMENT_SCREEN_LOGIN_ID")
        login_password = os.getenv("MANAGEMENT_SCREEN_PASSWORD")
        
        if not login_id or not login_password:
            print("ログイン情報が環境変数に設定されていません")
            return None, None
            
        print("ログイン情報を入力中...")
        # フォームに入力
        userid_input.send_keys(login_id)
        password_input.send_keys(login_password)
        
        # ログインボタンをクリック
        login_button = driver.find_element(By.ID, "login2")
        login_button.click()
        
        print("ログイン処理完了、ページの読み込みを待機中...")
        # ログイン後のページ読み込みを待機
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "ltl")))
        
        # 対戦募集リンクを探して遷移
        print("対戦募集ページへ移動...")
        schedule_link = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), '対戦募集')]")))
        schedule_link.click()
        
        # 対戦募集ページの読み込みを待機
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "lgTdl8")))
        time.sleep(2)  # ページの完全な読み込みを待機
        
        # タイムスタンプ付きのファイル名を生成
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = os.path.join(SCREENSHOT_DIR, f"game_recruitment_{timestamp}.png")
        
        # フルページスクリーンショットを撮影
        print("フルページスクリーンショットを撮影中...")
        
        # ページ全体の高さを取得
        total_height = driver.execute_script("return document.body.scrollHeight")
        print(f"ページ全体の高さ: {total_height}px")
        
        # ウィンドウサイズを取得
        window_width = driver.execute_script("return window.innerWidth")
        
        # ブラウザのウィンドウサイズをページ全体の高さに設定
        driver.set_window_size(window_width, total_height)
        time.sleep(1)
        
        # スクリーンショットを撮影
        driver.save_screenshot(screenshot_path)
        print(f"フルページスクリーンショット保存完了: {screenshot_path}")
        
        # 保存した画像を読み込んでバイナリデータとして返す
        with open(screenshot_path, 'rb') as f:
            screenshot_data = f.read()
        print("スクリーンショットの取得完了")
        return screenshot_data, screenshot_path
        
    except Exception as e:
        print(f"試合相手募集ページのスクリーンショット取得中にエラー: {str(e)}")
        return None, None


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

        # 試合相手の募集ページのスクリーンショットを取得
        screenshot_data, screenshot_path = get_game_recruitment_screenshot(driver)
        if screenshot_path:
            print(f"スクリーンショットファイル: {screenshot_path}")

        send_line_message(message, screenshot_path)
        print('\n送信メッセージ:')
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