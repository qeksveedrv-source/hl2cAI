import streamlit as st
import sqlite3
import re
import json
from google import genai
from google.genai import types

# ==========================================
# 基礎設定區
# ==========================================
API_KEY = st.secrets["GEMINI_API_KEY"]       
DB_PATH = "hl2c_LVR.db"             
TABLE_NAME = "records"            
MODEL_NAME = "gemini-3-flash-preview"     

# 初始化 Gemini Client (使用 Cache 避免重複連線)
@st.cache_resource
def get_gemini_client():
    return genai.Client(api_key=API_KEY)

client = get_gemini_client()

# ==========================================
# 核心邏輯功能
# ==========================================

def clean_sql(sql_text):
    """移除 Markdown 標籤並去除前後空白"""
    sql_text = re.sub(r'```sql', '', sql_text, flags=re.IGNORECASE)
    sql_text = re.sub(r'```', '', sql_text)
    return sql_text.strip()

def is_safe_sql(sql_str):
    """
    第一道防線：語法關鍵字檢查
    確保只允許 SELECT 查詢，並防止堆疊查詢 (;)
    """
    sql_clean = sql_str.strip().upper()
    if not sql_clean.startswith("SELECT"):
        return False
    
    # 檢查是否包含危險指令 (用正規表示式確保是獨立單字，避免誤殺地址名)
    forbidden_words = r'\b(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH)\b'
    if re.search(forbidden_words, sql_clean):
        return False
        
    return True

def ask_gemini_to_sql(user_prompt):
    """請 Gemini 轉譯 SQL (溫度設為 0.0 確保精準度)"""
    db_schema = f"""
資料表名稱: {TABLE_NAME}
欄位清單：
- area (TEXT): 鄉鎮市區 ('花蓮市', '吉安鄉')
- target_type (TEXT): 交易標的 ('房地(土地+建物)', '土地', '建物')
- address (TEXT): 完整建物門牌
- land_area (REAL): 土地面積(平方公尺)
- deal_date (INTEGER): 民國年月日 (例: 1150315 代表115年3月15日；2026年請用 BETWEEN 1150101 AND 1151231)
- build_type (TEXT): 建物型態 ('透天厝', '住宅大樓(11層含以上有電梯)', '華廈(10層含以下有電梯)', '公寓(5層含以下無電梯)')
- main_use (TEXT): 主要用途 ('住家用', '商業用')
- material (TEXT): 主要建材
- build_date (INTEGER): 建築完成年月 (例: 850612)
- total_build_area (REAL): 建物總面積(平方公尺)
- floor_level (TEXT): 移轉層次 ('一層', '二層')
- total_floors (TEXT/INTEGER): 總樓層數
- price (INTEGER): 總價元(整數)
- main_area (REAL): 主建物面積(平方公尺)
- ancillary_area (REAL): 附屬建物面積
- balcony_area (REAL): 陽台面積
- parking_type (TEXT): 車位類別
- parking_price (INTEGER): 車位總價元
- parking_area (REAL): 車位面積(平方公尺)
"""
    system_instruction = (
        "你是專業的 SQLite 資料庫分析師。請根據 Schema 將使用者的中文問題轉換為合法的 SQLite SELECT 語法。\n"
        "【強制規則】:\n"
        "1. 只允許輸出 SELECT 查詢語句，絕不包含任何 Markdown 包裝。\n"
        "2. 面積單位為「平方公尺」，若使用者提及「坪」，請自行在條件或欄位中換算（1坪 = 3.3058平方公尺）。\n"
        "3. 在 SQL 語句末尾務必加上 LIMIT 20（除非使用者指定更多，但最多不超過 50）。"
        "4. 注意：deal_date 是民國年月日（如 1150315；2026年請用 BETWEEN 1150101 AND 1151231）。\n"
        "5. 【台灣地名標準化規則】實價登錄資料庫中的縣市、鄉鎮市區與路名，官方一律使用正體「臺」（例如：臺北市、臺中市、臺南市、臺東縣）。當使用者提問中使用俗體「台」（如台中、台東、台北），你在生成 SQL 的 WHERE 條件時，務必主動將其全部轉換為官方標準的「臺」。"
        "3. 在 SQL 語句末尾務必加上 LIMIT 20（除非使用者指定更多，但最多不超過 50）。\n"
        "4. 注意：deal_date 是民國年月日（如 1150315；2026年請用 BETWEEN 1150101 AND 1151231）。\n"
        "5. 【台灣地名標準化規則】實價登錄資料庫中的縣市、鄉鎮市區與路名，官方一律使用正體「臺」（例如：臺北市、臺中市、臺南市、臺東縣）。當使用者提問中使用俗體「台」（如台中、台東、台北），你在生成 SQL 的 WHERE 條件時，務必主動將其全部轉換為官方標準的「臺」。"
        "6. 【訪價完整地址過濾規則】當使用者想要估價或訪價特定路名、街名或門牌時，請檢查他的提問中是否包含完整的「縣市與鄉鎮市區」（例如：花蓮縣吉安鄉、臺中市西屯區）。\n"
        "7. 【不完整地址之處理】若使用者詢問特定路名/門牌行情，卻【沒有】提供縣市與鄉鎮市區（例如僅輸入「吉昌二街行情」或「中正路100號估價」），絕對不允許對全表進行模糊匹配！請立刻停止查詢，並直接生成以下這句 SQL 指令：\n"
        "SELECT '請提供完整的「縣市與鄉鎮市區」（例如：花蓮縣吉安鄉），系統才能為您啟動極速定位並精準估價喔！' AS system_notice;\n"
        "這樣能引導使用者補充完整資訊。"
    )
    
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=f"資料庫結構:\n{db_schema}\n\n使用者問題：{user_prompt}",
        config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.0)
    )
    return clean_sql(response.text)

def query_database(sql_str, max_rows=30):
    """
    第二道防線：使用 URI 唯讀模式開啟 SQLite，從物理底層封鎖寫入與刪除
    並在 Python 端強制截斷資料筆數，保護效能
    """
    try:
        # uri=True 搭配 ?mode=ro 代表 Read-Only (唯讀模式)
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute(sql_str)
        
        # 強制最多只抓取 max_rows 筆，避免 AI 忘記 LIMIT 導致記憶體爆滿
        results = cursor.fetchmany(max_rows)
        columns = [desc[0] for desc in cursor.description]
        conn.close()
        
        # 將 Tuple 轉為字典清單，方便 AI 快速閱讀，省去解析時間
        formatted_data = [dict(zip(columns, row)) for row in results]
        return formatted_data, None
    except Exception as e:
        return None, str(e)

def ask_gemini_to_explain(user_prompt, formatted_data):
    """請 Gemini 翻譯為專家分析 (溫度設為 0.2 讓回答簡潔快速)"""
    system_instruction = (
        "你是一位親切、專業的台灣房地產分析專家。請根據提供的 JSON 查詢結果，用通順的繁體中文回答使用者。\n"
        "【重要規則】:\n"
        "1. 數據中的面積為平方公尺，回答時請貼心換算成台灣習慣的「坪」（除以 3.3058，四捨五入至小數第二位）。\n"
        "2. 重點條列成交行情與數據特徵，避免廢話，排版精簡舒適。"
    )
    
    # 轉成精簡的 JSON 字串傳給 AI
    data_context = json.dumps(formatted_data, ensure_ascii=False)
    
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=f"使用者提問：{user_prompt}\n\n查詢結果數據：\n{data_context}\n\n請解讀並回答。",
        config=types.GenerateContentConfig(
            system_instruction=system_instruction, 
            temperature=0.2 # 稍微降低溫度，生成更快更聚焦
        )
    )
    return response.text

# ==========================================
# Streamlit 網頁 UI 介面設計
# ==========================================

st.set_page_config(page_title="實價登錄 AI 助理", page_icon="🏠")
st.title("🏠 實價登錄 AI 智慧助理")
st.info(
    "查詢特定門牌或路段時，**請務必輸入「完整的縣市與鄉鎮市區」**\n"
    "*標準範例：**花蓮縣吉安鄉**吉昌二街xx號，20年30坪透天行情多少？*"
)


if "messages" not in st.session_state:
    st.session_state.messages = []

# 顯示歷史紀錄
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("sql"):
            with st.expander("🔍 查看 SQL 查詢指令"):
                st.code(message["sql"], language="sql")

# 處理使用者輸入
if user_input := st.chat_input("訪價請輸入完整地址及坪數"):
    
    standardized_input = user_input.replace("台", "臺")
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
        
    with st.chat_message("assistant"):
        with st.spinner("⚡ AI 正在查詢與計算行情中..."):
            try:
                # 階段 A: 生成 SQL
                generated_sql = ask_gemini_to_sql(user_input)
                
                with st.expander("🔍 查看 SQL 查詢指令"):
                    st.code(generated_sql, language="sql")
                
                # 階段 B: 安全檢查與查庫
                if is_safe_sql(generated_sql):
                    data, err = query_database(generated_sql)
                    
                    if err:
                        st.error(f"資料庫查詢發生語法錯誤: {err}")
                    elif data and len(data) > 0:
                        # 階段 C: 專家解讀
                        final_answer = ask_gemini_to_explain(user_input, data)
                        st.markdown(final_answer)
                        
                        st.session_state.messages.append({
                            "role": "assistant", 
                            "content": final_answer,
                            "sql": generated_sql
                        })
                    else:
                        no_data_msg = "📭 目前資料庫中查無符合條件的交易紀錄可參考。"
                        st.warning(no_data_msg)
                        st.session_state.messages.append({"role": "assistant", "content": no_data_msg, "sql": generated_sql})
                else:
                    safe_msg = "⚠️ 偵測到非 SELECT 查詢或潛在不安全指令，系統已攔截。"
                    st.error(safe_msg)
                    st.session_state.messages.append({"role": "assistant", "content": safe_msg, "sql": generated_sql})
                    
            except Exception as e:
                st.error(f"連線或解析發生錯誤: {e}")
