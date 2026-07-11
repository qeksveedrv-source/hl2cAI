import streamlit as st
import sqlite3
import re
from google import genai
from google.genai import types

# ==========================================
# 基礎設定區：請填入與您之前相同的設定
# ==========================================
API_KEY = st.secrets["GEMINI_API_KEY"]       
DB_PATH = "hl2c_LVR.db"             # 您的 SQLite 資料庫路徑
TABLE_NAME = "records"            # 您的資料表名稱
MODEL_NAME = "gemini-3-flash-preview"     # 使用的付費版模型

# 初始化 Gemini Client
@st.cache_resource
def get_gemini_client():
    return genai.Client(api_key=API_KEY)

client = get_gemini_client()

# ==========================================
# 核心邏輯功能（與先前相同）
# ==========================================

def clean_sql(sql_text):
    sql_text = re.sub(r'```sql', '', sql_text, flags=re.IGNORECASE)
    sql_text = re.sub(r'```', '', sql_text)
    return sql_text.strip()

def is_safe_sql(sql_str):
    forbidden_words = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE', 'REPLACE', 'TRUNCATE']
    sql_upper = sql_str.upper()
    for word in forbidden_words:
        if word in sql_upper: return False
    return True

def ask_gemini_to_sql(user_prompt):
    db_schema = f"""
資料表名稱: {TABLE_NAME}
欄位清單與說明：
- area (TEXT): 鄉鎮市區，例如：'花蓮市', '吉安鄉', '新城鄉'
- target_type (TEXT): 交易標的，例如：'房地(土地+建物)', '房地(土地+建物)+車位', '土地', '建物'
- address (TEXT): 土地位置建物門牌，包含完整地址
- land_area (REAL): 土地移轉總面積（平方公尺）
- deal_date (INTEGER): 交易年月日，台灣民國年格式，例如：1150315 代表民國115年3月15日
- build_type (TEXT): 建物型態，例如：'透天厝', '住宅大樓(11層含以上有電梯)', '華廈(10層含以下有電梯)', '公寓(5層含以下無電梯)'
- main_use (TEXT): 主要用途，例如：'住家用', '商業用', '國民住宅'
- material (TEXT): 主要建材，例如：'鋼筋混凝土造'
- build_date (INTEGER): 建築完成年月，格式如：850612
- total_build_area (REAL): 建物移轉總面積（平方公尺）
- floor_level (TEXT): 移轉層次，例如：'一層', '二層'
- total_floors (TEXT/INTEGER): 總樓層數，例如：'三層'
- price (INTEGER): 總價元，總交易金額（整數）
- main_area (REAL): 主建物面積（平方公尺）
- ancillary_area (REAL): 附屬建物面積（平方公尺）
- balcony_area (REAL): 陽台面積（平方公尺）
- parking_type (TEXT): 車位類別，例如：'坡道平面'
- parking_price (INTEGER): 車位總價元
- parking_area (REAL): 車位移轉總面積（平方公尺）
"""
    system_instruction = (
        "你是一個熟練的 SQLite 專家。請根據提供的資料庫結構（Schema），將使用者的中文問題轉換為合法的 SQLite SELECT 語法。\n"
        "【重要規則】:\n"
        "1. 只能生成 SELECT 查詢語句，絕對不能包含修改資料的指令。\n"
        "2. 不要包含任何 Markdown 包裝（如 ```sql），直接輸出純文字 SQL。\n"
        "3. 欄位與資料表名稱必須完全符合 Schema 定義。\n"
        "4. 注意：實價登錄的面積單位均為「平方公尺」，如果使用者問到「坪」，請在 SQL 中自行換算（1 坪 = 3.3058 平方公尺，或 平方公尺 * 0.3025 = 坪）。\n"
        "5. 注意：deal_date 是民國年月日（如 1150520），如果使用者詢問特定年份（如 2026 年），請在 SQL 中使用 115 開頭進行範圍查詢（例如 BETWEEN 1150101 AND 1151231）。"
        "6. 【核心效能規則】在生成的 SQL 語句末尾，除非使用者有明確要求數量，否則必須強制加上 LIMIT 20。絕對不允許不加 LIMIT 導致撈出成百上千筆資料，這會摧毀系統效能。"
    )
    
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=f"資料庫結構如下：\n{db_schema}\n\n使用者問題：{user_prompt}",
        config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.0)
    )
    return clean_sql(response.text)

def query_database(sql_str):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(sql_str)
        results = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        conn.close()
        return columns, results
    except Exception as e:
        return None, str(e)

def ask_gemini_to_explain(user_prompt, columns, results):
    system_instruction = (
        "你是一個台灣房地產專家。請根據資料庫的真實查詢結果，用親切、專業且流暢的繁體中文回答使用者的問題。\n"
        "如果結果中的面積是平方公尺，請在回答時貼心地幫使用者換算成台灣習慣的「坪數」（除以 3.3058）。"
    )
    data_context = f"欄位標頭: {columns}\n數據內容: {results}"
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=f"使用者提問：{user_prompt}\n\n資料庫查詢結果：\n{data_context}\n\n請整理並解讀上述數據回答使用者。",
        config=types.GenerateContentConfig(system_instruction=system_instruction)
    )
    return response.text

# ==========================================
# Streamlit 網頁 UI 介面設計
# ==========================================

# 設定網頁標題與圖示
st.set_page_config(page_title="實價登錄 AI 助理", page_icon="🏠")
st.title("🏠 實價登錄 AI 智慧助理")
st.caption("輸入您的對話，讓 Gemini 自動幫您查詢本地 SQLite 資料庫並提供專家房產分析報告。")

# 初始化歷史聊天紀錄（若不存在的話）
if "messages" not in st.session_state:
    st.session_state.messages = []

# 在網頁上顯示過去的對話紀錄
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        # 如果紀錄裡面有包含當時生成的 SQL，一併顯示出來
        if "sql" in message and message["sql"]:
            with st.expander("🔍 檢視此對話生成的 SQL 指令"):
                st.code(message["sql"], language="sql")

# 建立網頁最下方的聊天輸入對話框
if user_input := st.chat_input("例如：幫我查吉安鄉最近總價 1000 萬以下的大樓交易"):
    
    # 1. 顯示使用者的提問
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
        
    # 2. 處理 AI 的回應
    with st.chat_message("assistant"):
        # 建立一個加載中的動畫效果
        with st.spinner("AI 正在分析資料庫中..."):
            try:
                # 階段 A: 生成 SQL
                generated_sql = ask_gemini_to_sql(user_input)
                
                # 顯示一個可以展開的區塊讓使用者看 SQL
                with st.expander("🔍 檢視此對話生成的 SQL 指令"):
                    st.code(generated_sql, language="sql")
                
                # 安全過濾檢查
                if is_safe_sql(generated_sql):
                    # 階段 B: 查資料庫
                    cols, rows = query_database(generated_sql)
                    
                    if cols:
                        # 階段 C: 解讀結果
                        final_answer = ask_gemini_to_explain(user_input, cols, rows)
                        st.markdown(final_answer)
                        
                        # 存入對話紀錄
                        st.session_state.messages.append({
                            "role": "assistant", 
                            "content": final_answer,
                            "sql": generated_sql
                        })
                    else:
                        no_data_msg = f"資料庫查無符合條件的資料。 (錯誤訊息: {rows})"
                        st.warning(no_data_msg)
                        st.session_state.messages.append({"role": "assistant", "content": no_data_msg, "sql": generated_sql})
                else:
                    safe_msg = "⚠️ 偵測到不安全的 SQL 指令，系統已攔截執行。"
                    st.error(safe_msg)
                    st.session_state.messages.append({"role": "assistant", "content": safe_msg, "sql": generated_sql})
                    
            except Exception as e:
                error_msg = f"系統發生錯誤: {e}"
                st.error(error_msg)
