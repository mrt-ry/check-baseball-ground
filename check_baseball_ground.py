import os
from datetime import timedelta
from zoneinfo import ZoneInfo
import traceback
import re
import time
import base64
import requests
from PIL import Image
from dotenv import load_dotenv
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

load_dotenv()
BASE_URL = os.getenv("BASE_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_GROUP_ID = os.getenv("LINE_GROUP_ID")
GAME_RECRUITMENT_URL = os.getenv("GAME_RECRUITMENT_URL")
GIT_REPO = os.getenv("GIT_REPO")  # 形式: "username/repository"
GITHUB_BRANCH = os.getenv("GIT_BRANCH", "main")  # デフォルトはmain
# 動作確認用の一時スイッチ（通常運用では未設定=無効）
SKIP_PARKS = os.getenv("SKIP_PARKS", "").lower() == "true"  # 都立公園の取得をスキップ
DRY_RUN = os.getenv("DRY_RUN", "").lower() == "true"  # LINE通知・スクリーンショットのGitプッシュをしない

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
    if not GIT_REPO:
        print("GIT_REPOが設定されていません")
        return None
    
    # ファイル名とディレクトリ名を取得
    filename = os.path.basename(screenshot_path)
    
    # GitHub RawのURL形式: https://raw.githubusercontent.com/{user}/{repo}/{branch}/screenshots/{filename}
    raw_url = f"https://raw.githubusercontent.com/{GIT_REPO}/{GITHUB_BRANCH}/screenshots/{filename}"
    
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


PARK_MAX_ATTEMPTS = 3  # 1公園あたりの最大試行回数（初回＋リトライ2回）


def init_driver():
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,3000')  # 縦長のウィンドウサイズ
    # サイト側が出す alert（「データ通信を正しく行うことができませんでした」等）を自動で閉じる
    options.unhandled_prompt_behavior = "accept"
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(30)
    return driver


HOME_RETURN_ATTEMPTS = 2


def return_to_home(driver, wait):
    """ホーム画面に戻る。失敗しても例外は出さず、成功したかをTrue/Falseで返す。

    サイトが遅くて戻れないときに、ここでの失敗が全体のやり直しにならないようにする。
    """
    for attempt in range(1, HOME_RETURN_ATTEMPTS + 1):
        try:
            driver.get(BASE_URL)
            wait.until(EC.presence_of_element_located((By.ID, "daystart-home")))
            print("ホーム画面に戻る")
            time.sleep(3)
            return True
        except Exception as e:
            print(f"ホーム画面に戻れませんでした(試行 {attempt}/{HOME_RETURN_ATTEMPTS}): {type(e).__name__}")
    return False


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


JST = ZoneInfo("Asia/Tokyo")
WEEKDAY_JA = "月火水木金土日"


def resolve_date(day, youbi, today, week_index):
    """表の見出しの「日」と曜日から、実際の日付(date)を求める。

    week_index週目の見出しは、今日を基準に [today-6+7*i, today+6+7*i] の範囲にある。
    この範囲は13日間なので「日」の数字だけで一意に決まる。曜日が一致することも確認する。
    見つからない/曜日が合わない場合はNoneを返す。
    """
    start = today - timedelta(days=6) + timedelta(days=7 * week_index)
    for i in range(13):
        d = start + timedelta(days=i)
        if d.day == day and WEEKDAY_JA[d.weekday()] == youbi:
            return d
    return None


def parse_week_table(html, today=None, week_index=0):
    if today is None:
        today = datetime.now(JST).date()
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
                    resolved = resolve_date(int(date_str), day_str, today, week_index) if date_str.isdigit() else None
                    if resolved is None:
                        print(f"警告: 日付を特定できません: {date_str}({day_str}) 週{week_index + 1}")
                        date_list.append("")
                    else:
                        date_list.append(resolved)
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
                        "date": date_list[col],  # datetime.date
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
        week_results += parse_week_table(driver.page_source, today=datetime.now(JST).date(), week_index=week)
        if week < 3:
            table_html = driver.find_element(By.CSS_SELECTOR, "table#week-info").get_attribute("outerHTML")
            next_btn = wait.until(EC.element_to_be_clickable((By.ID, "next-week")))
            driver.execute_script("arguments[0].click();", next_btn)
            WebDriverWait(driver, 10).until(lambda d: d.find_element(By.ID, "week-info").get_attribute("outerHTML") != table_html)
            print(f"{week+2}周目への遷移")
    print(f"情報取得完了")
    return week_results


TOSHIMA_URL = os.getenv("TOSHIMA_URL", "https://www2.pf489.com/toshima/WebR/Home/WgR_ModeSelect")
TOSHIMA_FACILITY = "総合体育場"
TOSHIMA_SECTION_NAME = f"豊島区 {TOSHIMA_FACILITY}"  # 通知の見出し
TOSHIMA_ROW_NAME = "野球場"  # 総合体育場の中で対象とする施設（A面・B面がある）
TOSHIMA_MAX_DATES = 20  # サイト側の1回の選択上限
TOSHIMA_STATUS = {"○": "空き"}  # 時間帯の「△(施設に問合せ)」は通知しない
TOSHIMA_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def parse_toshima_timeslots(html):
    """豊島区「時間帯別空き状況」ページから、空き(○)の枠を取り出す。

    日付ごとに表が1つあり、行がA面/B面、列が時間帯。
    """
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for table in soup.find_all("table", class_="calendar"):
        head = table.find("thead")
        if not head:
            continue
        ths = head.find_all("th")
        m = TOSHIMA_DATE_RE.search(ths[0].get_text()) if ths else None
        if not m:
            continue
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        times = [th.get_text("", strip=True) for th in ths[2:]]  # 先頭2列は日付・定員
        body = table.find("tbody")
        if not body:
            continue
        for tr in body.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            face = tds[0].get_text(strip=True)
            for time_label, td in zip(times, tds[2:]):
                label = td.find("label")
                mark = label.get_text(strip=True) if label else ""
                if mark in TOSHIMA_STATUS:
                    result.append({
                        "date": d,
                        "face": face,
                        "time": time_label,
                        "status": TOSHIMA_STATUS[mark],
                    })
    return result


def get_toshima_availability(driver, wait):
    """豊島区 総合体育場（野球場 A面・B面）の、土日祝の空き枠を取得する。"""
    print(f"\n【豊島区 {TOSHIMA_FACILITY}】の空き枠検索開始")
    driver.get(TOSHIMA_URL)
    wait.until(EC.presence_of_element_located((By.ID, "category_20")))
    driver.execute_script("__doPostBack('ssCategory','20');")  # スポーツ施設
    wait.until(EC.url_contains("WgR_ShisetsuKensaku"))
    # 施設名のラベルから対象施設のチェックボックスを選ぶ
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "td.shisetsu input[name=checkShisetsu]")))
    clicked = driver.execute_script("""
        for (const td of document.querySelectorAll('td.shisetsu')) {
            const label = td.querySelector('label');
            if (label && label.textContent.trim() === arguments[0]) {
                td.querySelector('input[name=checkShisetsu]').click();
                return true;
            }
        }
        return false;
    """, TOSHIMA_FACILITY)
    if not clicked:
        # 「さらに読み込む」の先にある場合に備え、見つからなければエラーにする
        raise RuntimeError(f"施設「{TOSHIMA_FACILITY}」が一覧に見つかりません")
    driver.execute_script("__doPostBack('next','');")
    wait.until(EC.url_contains("WgR_ShisetsubetsuAkiJoukyou"))

    # 表示条件: 1ヶ月分・土日祝のみ（祝日の判定はサイト側に任せる）
    old_element = wait.until(EC.presence_of_element_located((By.ID, "radioPeriod1month")))
    driver.execute_script("""
        document.getElementById('radioPeriod1month').click();
        for (const id of ['checkYobi6', 'checkYobi7', 'checkYobi8']) {
            document.getElementById(id).click();
        }
        __doPostBack('hyouji', '');
    """)
    wait.until(EC.staleness_of(old_element))  # 再表示でページが読み込み直されるのを待つ
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.calendar td.shisetsu")))

    # 野球場の行で、○/△の日付を選ぶ（×・－は選べない）
    selected = driver.execute_script("""
        const max = arguments[1];
        let n = 0;
        for (const tr of document.querySelectorAll('tr')) {
            const name = tr.querySelector('td.shisetsu');
            if (!name || !name.textContent.includes(arguments[0])) continue;
            for (const input of tr.querySelectorAll('input[name=checkdate]')) {
                const mark = input.nextElementSibling ? input.nextElementSibling.textContent.trim() : '';
                if ((mark === '○' || mark === '△') && n < max) { input.click(); n++; }
            }
        }
        return n;
    """, TOSHIMA_ROW_NAME, TOSHIMA_MAX_DATES)
    print(f"  空きのある日付: {selected}日")
    if selected == 0:
        return []
    driver.execute_script("__doPostBack('next','');")
    wait.until(EC.url_contains("WgR_JikantaibetsuAkiJoukyou"))
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.calendar tbody tr")))
    slots = parse_toshima_timeslots(driver.page_source)
    print(f"  空き枠: {len(slots)}件")
    return slots


SUMIDA_URL = os.getenv("SUMIDA_URL", "https://yoyaku03.city.sumida.lg.jp/user/Home")
SUMIDA_FACILITY = "公園運動場"  # 施設選択で選ぶ施設
SUMIDA_ROW_NAME = "錦糸公園野球場"  # 公園運動場の中で対象とする室場（A面・B面）
SUMIDA_SECTION_NAME = f"墨田区 {SUMIDA_ROW_NAME}"  # 通知の見出し
SUMIDA_WEEKS = 6  # 先の何週間分を見るか
SUMIDA_DATE_RE = re.compile(r"(\d{4})年\s*(\d{1,2})月(\d{1,2})日")
SUMIDA_FACE_RE = re.compile(r"([ＡＢＣＤＥA-E])面")
# 日付を選ぶ段階: 土日祝（見出しのclassがsat/sun。祝日はsunで表示される）の、空き(vacant)・一部空き(some)の日
SUMIDA_SELECT_JS = """
    let n = 0;
    const heads = [...document.querySelectorAll('table.table-schedule thead th.month')];
    for (const tr of document.querySelectorAll('table.table-schedule tbody tr')) {
        const name = tr.querySelector('td');
        if (!name || !name.textContent.includes(arguments[0])) continue;
        const cells = [...tr.querySelectorAll('td.btn-group-toggle')];
        cells.forEach((td, i) => {
            const head = heads[i];
            const label = td.querySelector('label');
            if (!head || !label) return;
            const weekend = head.classList.contains('sat') || head.classList.contains('sun');
            const open = label.classList.contains('some') || label.classList.contains('vacant');
            if (weekend && open) { label.click(); n++; }
        });
    }
    return n;
"""


def parse_sumida_timeslots(html):
    """墨田区「時間帯別空き状況」ページから、空き(vacant)の枠を取り出す。

    日付ごとのブロック(div.events > div)に、室場(A面/B面)ごとの行があり、
    各コマは TimeFrom/TimeTo(例: 1630)を持つ。
    """
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for day in soup.select("div.events > div"):
        date_el = day.select_one(".events-date")
        m = SUMIDA_DATE_RE.search(date_el.get_text(" ", strip=True)) if date_el else None
        if not m:
            continue
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        for group in day.select("div.events-group"):
            name_el = group.select_one(".room-name span")
            name = name_el.get_text(strip=True) if name_el else ""
            if SUMIDA_ROW_NAME not in name:
                continue
            fm = SUMIDA_FACE_RE.search(name)
            face = (fm.group(1) + "面") if fm else name
            for cell in group.select("div.display-cells > div"):
                box = cell.select_one("div.btn-group-toggle")
                if not box or "vacant" not in (box.get("class") or []):
                    continue
                t_from = cell.select_one("input[name$=TimeFrom]")
                t_to = cell.select_one("input[name$=TimeTo]")
                if not t_from or not t_to:
                    continue
                f, t = int(t_from["value"]), int(t_to["value"])
                result.append({
                    "date": d,
                    "face": face,
                    "time": f"{f // 100}:{f % 100:02d}～{t // 100}:{t % 100:02d}",
                    "status": "空き",
                })
    return result


def _click_visible_button(driver, text):
    """表示中のボタンのうち、文言が一致する最後のものをクリックする。"""
    buttons = [b for b in driver.find_elements(By.XPATH, f"//button[contains(., '{text}')]") if b.is_displayed()]
    if not buttons:
        raise RuntimeError(f"ボタン「{text}」が見つかりません")
    driver.execute_script("arguments[0].click();", buttons[-1])


def _sumida_week_header(driver):
    return driver.execute_script(
        "return [...document.querySelectorAll('table.table-schedule thead th.month')]"
        ".map(t => t.textContent.replace(/\\s+/g, '')).join(',');"
    )


def get_sumida_availability(driver, wait):
    """墨田区 錦糸公園野球場（A面・B面）の、土日祝の空き枠を取得する。"""
    print(f"\n【墨田区 {SUMIDA_ROW_NAME}】の空き枠検索開始")
    driver.get(SUMIDA_URL)
    wait.until(EC.presence_of_element_located((By.CLASS_NAME, "tab-name")))
    time.sleep(1)  # Vueの描画待ち
    # 室場種類から探す → スポーツ施設 → 野球場 → 検索
    driver.find_element(By.XPATH, "//*[contains(@class,'tab-name') and contains(.,'室場種類から探す')]").click()
    wait.until(EC.element_to_be_clickable((By.XPATH, "//label[contains(.,'スポーツ施設')]"))).click()
    wait.until(EC.element_to_be_clickable((By.XPATH, "//label[normalize-space(.)='野球場']"))).click()
    _click_visible_button(driver, "検索")
    # 施設選択 → 公園運動場
    wait.until(EC.element_to_be_clickable((By.XPATH, f"//label[contains(.,'{SUMIDA_FACILITY}')]"))).click()
    _click_visible_button(driver, "次へ進む")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.table-schedule tbody tr")))

    slots = []
    for week in range(SUMIDA_WEEKS):
        header = _sumida_week_header(driver)
        selected = driver.execute_script(SUMIDA_SELECT_JS, SUMIDA_ROW_NAME)
        print(f"  {week + 1}週目: 土日祝で空きのある日 {selected}件")
        if selected:
            _click_visible_button(driver, "次へ進む")
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.events-group")))
            slots += parse_sumida_timeslots(driver.page_source)
            _click_visible_button(driver, "前に戻る")  # 同じ週の画面に戻る
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.table-schedule tbody tr")))
        if week < SUMIDA_WEEKS - 1:
            next_btn = wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(.,'次の期間')]|//a[contains(.,'次の期間')]")))
            driver.execute_script("arguments[0].click();", next_btn)
            wait.until(lambda d: _sumida_week_header(d) != header)
    print(f"  空き枠: {len(slots)}件")
    return slots


def time_sort_key(slot):
    m = re.match(r"([０-９0-9]+)", slot["time"])
    if m:
        zenkaku = str.maketrans('０１２３４５６７８９', '0123456789')
        t = m.group(1).translate(zenkaku)
        return int(t)
    return 0


def start_time_label(time_range):
    """「8:00～10:00」→「8時」、「18:30～20:30」→「18時30分」"""
    m = re.match(r"\s*(\d{1,2}):(\d{2})", time_range)
    if not m:
        return time_range
    hour, minute = int(m.group(1)), int(m.group(2))
    return f"{hour}時" if minute == 0 else f"{hour}時{minute}分"


def format_slot_lines(slots):
    lines = []
    for slot in sorted(slots, key=lambda x: (x["date"], time_sort_key(x), x.get("face", ""))):
        d = slot["date"]
        youbi = WEEKDAY_JA[d.weekday()]
        time_hankaku = slot['time'].translate(str.maketrans('０１２３４５６７８９', '0123456789'))
        face = slot.get("face")
        if face:  # 豊島区(総合体育場): A面/B面あり
            # 既存に合わせ「8時：A面」の形にする（開始時刻のみ。載っている枠はすべて空き）
            face_hankaku = face.translate(str.maketrans('ＡＢ', 'AB'))
            lines.append(f"・{d.month}/{d.day}({youbi}) {start_time_label(slot['time'])}：{face_hankaku}")
        else:
            lines.append(f"・{d.month}/{d.day}({youbi}) {time_hankaku}：{slot['status']}枠")
    return lines


def make_line_message(park_results):
    now = datetime.now(JST)
    lines = [f"📣 グラウンドの空き情報（{now.month}/{now.day} {now.strftime('%H:%M')}時点）\n"]
    # 都立公園（既存サイト）→ 豊島区の順に並べる。予約サイトが別なので、それぞれ専用のリンクを付ける
    for park_name, slots in park_results.items():
        if not slots or park_name in (TOSHIMA_SECTION_NAME, SUMIDA_SECTION_NAME):
            continue
        lines.append(f"【{park_name}】")
        lines += format_slot_lines(slots)
        lines.append("")
    if BASE_URL:
        lines.append("🔗 都立公園の予約サイト")
        lines.append(BASE_URL)
        lines.append("")
    # 豊島区・墨田区（予約サイトが別なので、それぞれ専用のリンクを付ける）
    for section_name, section_url in ((TOSHIMA_SECTION_NAME, TOSHIMA_URL), (SUMIDA_SECTION_NAME, SUMIDA_URL)):
        section_slots = park_results.get(section_name)
        if not section_slots:
            continue
        lines.append(f"【{section_name}】")
        lines += format_slot_lines(section_slots)
        lines.append(f"🔗 {section_name.split()[0]}の予約サイト")
        lines.append(section_url)
        lines.append("")
    return "\n".join(lines).rstrip("\n")

# スクリーンショットの保存先ディレクトリ
SCREENSHOT_DIR = "screenshots"
if not os.path.exists(SCREENSHOT_DIR):
    os.makedirs(SCREENSHOT_DIR)

RECRUITMENT_PAGE_LOAD_TIMEOUT = 180  # 募集ページは遅いことがあるため、読み込み待ちを長めにする
RECRUITMENT_MAX_ATTEMPTS = 2


def get_game_recruitment_screenshot(driver):
    """募集ページのスクリーンショットを取得。失敗したら1回だけやり直す。"""
    if not GAME_RECRUITMENT_URL:
        print("GAME_RECRUITMENT_URLが設定されていないためスキップ")
        return None, None
    for attempt in range(1, RECRUITMENT_MAX_ATTEMPTS + 1):
        print(f"\n募集ページのスクリーンショット取得 (試行 {attempt}/{RECRUITMENT_MAX_ATTEMPTS})")
        data, path = _get_game_recruitment_screenshot_once(driver)
        if path:
            return data, path
        if attempt < RECRUITMENT_MAX_ATTEMPTS:
            print("5秒待ってからやり直します（ログイン状態をリセット）")
            time.sleep(5)
            try:
                driver.delete_all_cookies()
            except Exception as e:
                print(f"cookie削除に失敗: {repr(e)}")
    return None, None


def _get_game_recruitment_screenshot_once(driver):
    try:
        print(f"対戦相手募集ページにアクセス: {GAME_RECRUITMENT_URL}")
        driver.set_page_load_timeout(RECRUITMENT_PAGE_LOAD_TIMEOUT)
        try:
            driver.get(GAME_RECRUITMENT_URL)
        except TimeoutException:
            # 読み込みが終わらなくても、ログインフォームが出ていれば先に進める
            print(f"警告: ページ読み込みが{RECRUITMENT_PAGE_LOAD_TIMEOUT}秒を超えたため読み込みを中断して続行します")
            driver.execute_script("window.stop();")
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
        park_results = {}
        if SKIP_PARKS:
            print("SKIP_PARKS=true のため、都立公園の取得をスキップします")
            park_list = []
        else:
            park_list = get_park_list(driver, today)
        home_ok = True  # 直前にホーム画面へ戻れたか
        for park in park_list:
            for attempt in range(1, PARK_MAX_ATTEMPTS + 1):
                succeeded = False
                if not home_ok:
                    home_ok = return_to_home(driver, wait)
                try:
                    print(f"\n【{park['name']}】の空き枠検索開始 (試行 {attempt}/{PARK_MAX_ATTEMPTS})")
                    slots = get_park_availability(driver, park, wait)
                    park_results[park['name']] = slots
                    succeeded = True
                except Exception as e:
                    print(f"公園「{park['name']}」の処理中にエラー(試行 {attempt}/{PARK_MAX_ATTEMPTS}): {type(e).__name__}: {str(e)}")
                    try:
                        print(f"  現在のURL: {driver.current_url}")
                        print(f"  ページタイトル: {driver.title}")
                    except Exception as e2:
                        print(f"  ページ情報の取得失敗: {type(e2).__name__}")
                    traceback.print_exc()
                finally:
                    home_ok = return_to_home(driver, wait)
                if succeeded:
                    break
                if attempt < PARK_MAX_ATTEMPTS:
                    print(f"公園「{park['name']}」をリトライします")
                else:
                    print(f"公園「{park['name']}」は{PARK_MAX_ATTEMPTS}回失敗したためスキップします")
        # 豊島区・墨田区（既存の取得が失敗しても影響しないよう、それぞれ独立してリトライ）
        for section_name, fetch in (
            (TOSHIMA_SECTION_NAME, get_toshima_availability),
            (SUMIDA_SECTION_NAME, get_sumida_availability),
        ):
            for attempt in range(1, PARK_MAX_ATTEMPTS + 1):
                try:
                    park_results[section_name] = fetch(driver, wait)
                    break
                except Exception as e:
                    print(f"{section_name}の処理中にエラー(試行 {attempt}/{PARK_MAX_ATTEMPTS}): {type(e).__name__}: {str(e)}")
                    traceback.print_exc()
                    if attempt < PARK_MAX_ATTEMPTS:
                        time.sleep(3)
                    else:
                        print(f"{section_name}は失敗したためスキップします")
        message = make_line_message(park_results)

        if DRY_RUN:
            print("DRY_RUN=true のため、LINE通知とスクリーンショット取得は行いません")
            print('\n（送信しなかった）メッセージ:')
            print(message)
            return

        # 試合相手の募集ページのスクリーンショットを取得
        screenshot_data, screenshot_path = get_game_recruitment_screenshot(driver)
        if screenshot_path:
            print(f"スクリーンショットファイル: {screenshot_path}")

        send_line_message(message, screenshot_path)
        print('\n送信メッセージ:')
        print(message)
    finally:
        try:
            driver.quit()
        except Exception as e:
            print(f"driver.quit()に失敗: {repr(e)}")
        print("\n検索完了")


def main_with_retry(max_retries=5):
    for attempt in range(1, max_retries + 1):
        try:
            main()
            break
        except Exception as e:
            print(f"[リトライ] {attempt}回目でエラー発生: {repr(e)}")
            traceback.print_exc()
            if attempt == max_retries:
                print("最大リトライ回数に達したため処理を中断します。")
                raise SystemExit(1)
            else:
                print("60秒待ってから再実行します...")
                time.sleep(60)

if __name__ == "__main__":
    main_with_retry(max_retries=5) 