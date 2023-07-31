from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from oauth2client.service_account import ServiceAccountCredentials
import requests
import random
import time
import gspread
import telegram
import os
import logging
import logging.handlers


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger_file_handler = logging.handlers.RotatingFileHandler(
    "status.log",
    maxBytes=1024 * 1024,
    backupCount=1,
    encoding="utf8",
)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger_file_handler.setFormatter(formatter)
logger.addHandler(logger_file_handler)

SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
    ]

USER_AGENT = ['Mozilla/5.0 (Windows NT 6.3; Win64; x64)\
    AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.132\
    Safari/537.36', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_2)\
    AppleWebKit/601.3.9 (KHTML, like Gecko) Version/9.0.2 Safari/601.3.9',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:15.0) Gecko/20100101 Firefox/15.0.1']

JSON_FILE = './google.json'

def change_date_format(date):
    """
    날짜 포맷을 년.월.일. 형식으로 변경하는 함수
    네이버 뉴스의 경우
    0분전, 0시간 전, 0일전 등 7일 내 뉴스는
    년.월.일이 아닌 다른 포맷으로 표시되므로 날짜를 통일해주는 함수가 필요함
    """
    current_time = datetime.now()
    date = date.replace(" ", "")
    if date.endswith('분전'):
        minutes = int(date[:-2])
        date = current_time - timedelta(minutes=minutes)

    elif date.endswith('시간전'):
        hours = int(date[:-3])
        date = current_time - timedelta(hours=hours)

    elif date.endswith('일전'):
        days = int(date[:-2])
        date = current_time - timedelta(days=days)

    else:
        date = datetime.strptime(date, '%Y.%m.%d.')
    return date.strftime("%Y-%m-%d")


def send_telegram_message(article):
    bot = telegram.Bot(token=os.environ["TELEGRAM_TOKEN"])
    message = f"""[{article['날짜']}]\n{article['발행사']}의 기사 1건이 보도되었습니다.\n기사제목 : {article['제목']}\n{article['링크']}"""
    bot.sendMessage(chat_id=os.environ["CHAT_ID"], text=message)


def connect_file(SHEET_FILE_URL):
    """구글 스프레드 시트에 접속 연결"""   
    credentials = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, SCOPE)
    gc = gspread.authorize(credentials)
    doc = gc.open_by_url(SHEET_FILE_URL)
    worksheet = doc.worksheet('시트1')
    return worksheet


def connect_news_db():
    return connect_file(os.environ["NEWS_DB"])


def next_available_row(worksheet):
    """제일 최근 기사를 가져오기 위해 제일 하단 row로 접근"""
    str_list = list(filter(None, worksheet.col_values(1)))
    return str(len(str_list))


def get_all_keywords_col():
    ws = connect_news_db()
    return ws.get_all_values()


def write_file(worksheet, data):
    worksheet.append_row(data)


def retrieve_url_col():
    return set([i[3] for i in get_all_keywords_col()])


def write_news_db(worksheet, article):
    write_file(worksheet, [
        article['제목'],
        article['날짜'],
        article['발행사'],
        article['링크'],
        article['요약'],
        article['검색어'],
        ])


def write_log_db(worksheet, timestamp, message):
    write_file(worksheet, [timestamp, message])


def get_article(keyword, start_idx, saved_news, date_from, date_to):
    """네이버 기사 가져오기"""
    query = f'\"{keyword}\"'
    search_date_from = date_from.replace("-", ".")
    search_date_to = date_to.replace("-", ".")
    params = {
        'where': 'news',
        'query': query,
        'sm': 'tab_opt',
        'sort': 1, # 0: 관련도순 1: 최신순 2: 오래된순
        'photo': 0,
        'field': 0,
        'pd': 3, # 기간 설정과 관련된 키워드, 직접 입력시 3 / 전체 0 / 1시간 7 / 1주일 1 등 
        'ds': search_date_from, # 시작일
        'de': search_date_to, # 종료일
        'docid': '',
        'related': 0,
        'mynews': 0,
        'nso': 'so:dd,p:all,a:all',
        'office_type': 0,
        'office_section_code': 0,
        'news_office_checked': '',
        'is_sug_officeid': 0,
        'start': start_idx,
    }
    
    # html 요청
    base_url = 'https://search.naver.com/search.naver?'
    req = requests.get(base_url, params=params, headers={'User-Agent': random.choice(USER_AGENT)})

    if req.status_code != 200:
        logger.error(f'The connection is not valid! - code is {req.status_code}')
        return []
    
    # 파싱을 위한 뷰티풀 수프 객체 선언
    soup = BeautifulSoup(req.text, 'html.parser')
    news_list = soup.select('div.group_news div.news_area')
    
    results = []
    date_from = datetime.strptime(date_from, '%Y-%m-%d')
    date_to = datetime.strptime(date_to, '%Y-%m-%d')
    # 페이지당 기사 순회
    for news in news_list:
        date = change_date_format(news.select('span.info')[-1].text)
        current_date = datetime.strptime(date, '%Y-%m-%d')
        if current_date >= date_from and current_date <= date_to:
            publishing_company = news.select('a.info')
            is_naver = publishing_company[1]['href'] \
                if len(publishing_company) > 1 else '없음'
            title = news.select_one('a.news_tit')['title']
            url = news.select_one('a.news_tit')['href']
            description = news.select_one('a.api_txt_lines.dsc_txt_wrap').text
            article = {
                '제목': title,
                '날짜': date,
                '발행사': publishing_company[0].text,
                '네이버 발행': is_naver,
                '링크': url,
                '요약': description,
                '검색어': keyword
            }
            if url in saved_news: # google sheet의 최신 기사와 바교하여, 같은 경우 프로그램 종료.
                break
            results.append(article)
        else:
            logger.info('검색기간에 맞지 않는 기사이므로 저장하지 않습니다.')
            break
        
    return results


def main(keyword):
    
    idx = 1
    today = datetime.now().strftime('%Y-%m-%d')
    while True:
        load_news_db = connect_news_db()
        try:
            saved_news = retrieve_url_col() # google sheet 시트 내 키워드별 기사 링크 주소 목록
        except gspread.exceptions.APIError:
            saved_news = None
        
        articles = get_article(keyword, idx, saved_news, date_from='2023-01-01', date_to=today)
        
        if not articles:
            logger.info(f'{keyword} 검색어로 더이상 검색된 뉴스결과가 없으므로, 프로그램을 종료합니다')
            load_news_db.sort((2, 'asc'))
            break
        else:
            article_cnt = len(articles)
            for _ in range(article_cnt):
                article = articles.pop()
                write_news_db(load_news_db, article)
                send_telegram_message(article)
            logger.info(f'총 {article_cnt}건의 보도자료가 db에 기록되었습니다')
            idx += 10
        time.sleep(5)


if __name__ == "__main__":
    keywords = ['제11전투비행단', '11전비']
    for keyword in keywords:
        main(keyword)